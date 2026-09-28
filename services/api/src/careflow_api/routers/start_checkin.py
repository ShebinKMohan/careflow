"""Welcome-screen QR entry: anonymous until consent and patient identification on the phone.

The entry token is scoped to a kiosk and has its own audience. It is never a device credential.
A Redis lock serializes claims, and a hashed claimant/request proof makes response recovery safe.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import uuid
from typing import Annotated, Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select

from careflow_api.config import Settings, get_settings
from careflow_api.consent import get_consent, record_consent
from careflow_api.deps import Principal, require_roles
from careflow_api.handover import claim_proof, mark_handed_over
from careflow_api.intake.runtime import IntakeRuntime
from careflow_api.models import Device
from careflow_api.ratelimit import rate_limit
from careflow_api.rls import clinic_session
from careflow_api.routers.handover import CLOSED, redis_of, signing_key
from careflow_api.routers.sessions import (
    patient_token,
    pending_question,
    runtime,
    session_row,
    set_patient_details,
    start_keypad_session,
    utc_now,
)
from careflow_contracts.api_models import (
    CheckinResult,
    KeypadCheckinRequest,
    PatientType,
    QrToken,
    StartClaimRequest,
    StartQrRequest,
)
from careflow_contracts.generated.structured_facts_schema import Language

router = APIRouter(tags=["checkin"])
ENTRY_AUDIENCE = "careflow-phone-entry"
ENTRY_TTL = dt.timedelta(minutes=10)
CLAIM_TTL = 15 * 60


def verify_entry(secret: str, token: str) -> dict[str, Any]:
    """Reject handover/API tokens, malformed identifiers, forged and expired entry codes."""
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=ENTRY_AUDIENCE,
            options={"require": ["clinic_id", "device_id", "language", "jti", "iat", "exp"]},
        )
        uuid.UUID(claims["clinic_id"])
        uuid.UUID(claims["device_id"])
        uuid.UUID(claims["jti"])
        Language(claims["language"])
        return claims
    except (jwt.PyJWTError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="This welcome code expired or is invalid. Scan the kiosk code again.",
        ) from exc


@router.post("/v1/checkin/start/qr", response_model=QrToken)
async def issue_start_qr(
    body: StartQrRequest,
    principal: Annotated[Principal, Depends(require_roles("kiosk_device"))],
    settings: Annotated[Settings, Depends(get_settings)],
) -> QrToken:
    """Issue a short-lived entry capability without creating or exposing any patient session."""
    now = utc_now()
    expires_at = now + ENTRY_TTL
    claims = {
        "clinic_id": str(principal.clinic_id),
        "device_id": principal.device_id,
        "language": body.language.value if body.language else "en",
        "jti": str(uuid.uuid4()),
        "aud": ENTRY_AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return QrToken(
        qr_token=jwt.encode(claims, signing_key(settings), algorithm="HS256"),
        expires_at=expires_at,
    )


@router.post(
    "/v1/checkin/start/claim",
    response_model=CheckinResult,
    response_model_exclude_none=True,
    dependencies=[Depends(rate_limit("entry-claim", limit=10, window_seconds=60))],
)
async def claim_start_qr(
    body: StartClaimRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    intake: Annotated[IntakeRuntime, Depends(runtime)],
) -> CheckinResult:
    """Identify and consent on the phone, then continue with its scoped patient credential."""
    claims = verify_entry(signing_key(settings), body.qr_token)
    if not body.consent.data_use:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Please agree to share your answers with the care team, or ask at reception.",
        )
    child = body.patient.patient_type is PatientType.child
    if child and body.patient.age_years is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="A child's age is required."
        )
    if not child and (body.patient.age_years is not None or body.patient.age_months is not None):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="An adult's age comes from the record."
        )
    clinic_id = uuid.UUID(claims["clinic_id"])
    async with clinic_session(clinic_id) as db:
        device = (
            await db.execute(select(Device).where(Device.id == uuid.UUID(claims["device_id"])))
        ).scalar_one_or_none()
    if device is None or not device.is_active or device.kind != "kiosk":
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="This kiosk is no longer available."
        )
    principal = Principal(
        subject=claims["device_id"],
        role="kiosk_device",
        clinic_id=clinic_id,
        token_id=claims["jti"],
        device_id=claims["device_id"],
    )
    redis = redis_of(request)
    # One public welcome ticket supports several phones; the random claimant key owns a visit.
    # Neither patient digits nor the claimant's secret is retained in Redis.
    proof = claim_proof(body.claim_key)
    key = f"checkin:entry:{claims['jti']}:{proof}"
    fingerprint = hmac.new(
        body.claim_key.encode(),
        body.model_dump_json(exclude={"qr_token", "claim_key"}).encode(),
        hashlib.sha256,
    ).hexdigest()
    lock = redis.lock(f"{key}:lock", timeout=120, blocking=False)
    if not await lock.acquire():
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Your check-in is being prepared. Try again shortly."
        )
    try:
        saved = await redis.get(key)
        record: dict[str, Any]
        if saved:
            record = json.loads(saved)
            if not hmac.compare_digest(record["proof"], proof) or not hmac.compare_digest(
                record["fingerprint"], fingerprint
            ):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail="This welcome code was already used. Scan the kiosk code again.",
                )
            session_id = record["session_id"]
        else:
            session_id = str(uuid.uuid5(uuid.UUID(claims["jti"]), proof))
            record = {"proof": proof, "fingerprint": fingerprint, "session_id": session_id}
            # Reserve before database work. A stable session UUID resumes partial creation safely.
            await redis.set(key, json.dumps(record), ex=CLAIM_TTL)
        if not record.get("identified"):
            try:
                result = await start_keypad_session(
                    KeypadCheckinRequest(
                        clinic_id=str(clinic_id),
                        kiosk_id=claims["device_id"],
                        digits=body.digits,
                        language=body.language or claims["language"],
                    ),
                    principal,
                    intake,
                    settings,
                    session_id=uuid.UUID(session_id),
                )
            except HTTPException as exc:
                if exc.status_code == status.HTTP_404_NOT_FOUND:
                    # An incorrect number has not created a session; allow the phone to correct it.
                    await redis.delete(key)
                raise
            session_id = result.session_id
            record["identified"] = True
            await redis.set(key, json.dumps(record), ex=CLAIM_TTL)
        row = await session_row(principal, session_id)
        if row.status in CLOSED:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="This visit has ended.")
        # Every stage after recording the session can safely resume after a lost connection.
        if await get_consent(clinic_id, session_id) is None:
            await record_consent(
                clinic_id,
                session_id,
                body.consent,
                language=row.language,
                events=request.app.state.events,
                now=utc_now(),
            )
        phone_principal = Principal(
            subject=f"session:{session_id}",
            role="patient_session",
            clinic_id=clinic_id,
            token_id=claims["jti"],
            session_id=session_id,
            channel="phone",
        )
        await set_patient_details(session_id, body.patient, phone_principal, intake)
        await intake.update(session_id, {"channel": "phone"})
        await mark_handed_over(redis, session_id, claims["jti"])
        return CheckinResult(
            session_id=session_id,
            session_token=patient_token(
                settings, clinic_id=clinic_id, session_id=session_id, channel="phone"
            ),
            patient_display=row.patient_display or "Unknown",
            language=Language(row.language),
            first_question=pending_question(await intake.state(session_id)),
        )
    finally:
        await lock.release()
