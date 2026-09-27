"""The QR hand-over (M2-06): the kiosk shows a one-time code, the phone claims the session.

`POST /v1/sessions/{id}/qr` (the kiosk's device token, or the session's kiosk-channel patient
token) issues a 120-second token for one session. `POST /v1/checkin/qr/claim` needs no login and
is rate-limited per IP: a valid, unspent token for an open session returns a phone-channel patient
token and the question the intake is waiting on. The claim marks the session handed over (the
kiosk's tokens are refused from then on), sets the graph's `channel` to phone, and sends the kiosk
the `session.handed_over` frame so it can forget the patient. Every step after the spend is safe
to repeat, so the phone that claimed can ask again when the answer was lost (same code, same
`claim_key`, ADR-048). `POST /v1/sessions/{id}/renew` lets that phone swap a still-valid token for
a new one during a long wait.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select

from careflow_api.config import Settings, get_settings
from careflow_api.deps import Principal, require_phone_session, require_roles
from careflow_api.events.frames import SESSION_HANDED_OVER, publish_frame
from careflow_api.handover import (
    InvalidHandover,
    claim_proof,
    handed_over,
    issue,
    mark_handed_over,
    same_claimant,
    spend,
    verify,
)
from careflow_api.intake.runtime import IntakeRuntime
from careflow_api.models import PatientSession
from careflow_api.ratelimit import rate_limit
from careflow_api.rls import clinic_session
from careflow_api.routers.sessions import (
    PATIENT_TOKEN_TTL,
    own_session,
    patient_token,
    pending_question,
    runtime,
    session_row,
    touch,
    utc_now,
)
from careflow_contracts.api_models import CheckinResult, QrClaimRequest, QrToken, SessionRenewal
from careflow_contracts.generated.structured_facts_schema import Language

logger = logging.getLogger(__name__)
router = APIRouter(tags=["checkin"])

CLOSED: Final = frozenset({"closed", "no_show"})
CLAIMS_PER_MINUTE: Final = 10
#: How long after check-in the phone may keep renewing its token: a long emergency wait, bounded.
PHONE_VISIT_LIMIT: Final = dt.timedelta(hours=12)


def signing_key(settings: Settings) -> str:
    if settings.handover_signing_key is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="The QR hand-over is not configured."
        )
    return settings.handover_signing_key.get_secret_value()


def redis_of(request: Request):  # noqa: ANN201 - the app's async Redis client
    return request.app.state.redis


async def on_the_phone(intake: IntakeRuntime, session_id: str) -> bool:
    """Handed over for good: the graph's `channel` outlives the 2-hour Redis mark."""
    state: dict[str, Any] = dict(await intake.state(session_id) or {})
    return state.get("channel") == "phone"


@router.post("/v1/sessions/{session_id}/qr", response_model=QrToken)
async def issue_qr(
    session_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_roles("kiosk_device", "patient_session"))],
    settings: Annotated[Settings, Depends(get_settings)],
    intake: Annotated[IntakeRuntime, Depends(runtime)],
) -> QrToken:
    """A one-time, 120-second hand-over code for this session; only the kiosk may ask."""
    if principal.role == "patient_session":
        own_session(principal, session_id)
        if principal.channel == "phone":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only the kiosk can hand over.")
    row = await session_row(principal, session_id)
    if principal.role == "kiosk_device" and str(row.device_id) != principal.device_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Another kiosk checked this in.")
    if (
        row.status in CLOSED
        or await handed_over(redis_of(request), session_id)
        or await on_the_phone(intake, session_id)
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This session cannot be handed over.")
    token, expires_at = issue(
        signing_key(settings),
        session_id=session_id,
        clinic_id=str(principal.clinic_id),
        now=utc_now(),
    )
    return QrToken(qr_token=token, expires_at=expires_at)


@router.post(
    "/v1/checkin/qr/claim",
    response_model=CheckinResult,
    response_model_exclude_none=True,
    dependencies=[Depends(rate_limit("qr-claim", limit=CLAIMS_PER_MINUTE, window_seconds=60))],
)
async def claim_qr(
    body: QrClaimRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    intake: Annotated[IntakeRuntime, Depends(runtime)],
) -> CheckinResult:
    """The phone claims the session: 401 for an expired or forged code, 409 for a spent code or a
    closed or already handed-over session. The phone that claimed a code may send it again with
    its `claim_key` while the code is valid, and gets a new credential for the same session."""
    try:
        claims = verify(signing_key(settings), body.qr_token)
    except InvalidHandover as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="This code is not valid.") from exc
    redis = redis_of(request)
    again = False
    if not await spend(redis, claims.jti, claim_proof(body.claim_key)):
        if not await same_claimant(redis, claims.jti, body.claim_key):
            raise HTTPException(status.HTTP_409_CONFLICT, detail="This code was already used.")
        again = True  # this phone claimed it, and the first answer never reached it
    clinic_id = uuid.UUID(claims.clinic_id)
    try:
        key = uuid.UUID(claims.session_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="This code is not valid.") from exc
    async with clinic_session(clinic_id) as db:
        row = (
            await db.execute(select(PatientSession).where(PatientSession.id == key))
        ).scalar_one_or_none()
    if row is None or row.status in CLOSED:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This session is closed.")
    handed_before = not again and await on_the_phone(intake, claims.session_id)
    if handed_before or not await mark_handed_over(redis, claims.session_id, claims.jti):
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="This session was already handed over."
        )
    await intake.update(claims.session_id, {"channel": "phone"})
    token = patient_token(
        settings, clinic_id=clinic_id, session_id=claims.session_id, channel="phone"
    )
    question = pending_question(await intake.state(claims.session_id))
    await publish_frame(
        redis,
        frame_type=SESSION_HANDED_OVER,
        clinic_id=claims.clinic_id,
        session_id=claims.session_id,
        data={"session_id": claims.session_id},
        now=utc_now(),
        stream=settings.event_stream,
    )
    await touch(clinic_id, claims.session_id)
    logger.info("checkin.qr_claim", extra={"session_id": claims.session_id, "again": again})
    return CheckinResult(
        session_id=claims.session_id,
        session_token=token,
        patient_display=row.patient_display or "Unknown",
        language=Language(row.language),
        first_question=question,
    )


@router.post("/v1/sessions/{session_id}/renew", response_model=SessionRenewal)
async def renew_phone_token(
    session_id: str,
    principal: Annotated[Principal, Depends(require_phone_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SessionRenewal:
    """A new 30-minute phone token for a still-valid one, while the visit is open and at most
    12 hours after check-in. Only the phone's own token: a kiosk-channel token (revoked after the
    hand-over or not) or a device gets 403 or 401 first, so renewal never revives the kiosk's
    credential, and an expired phone token cannot renew itself."""
    own_session(principal, session_id)
    row = await session_row(principal, session_id)
    now = utc_now()
    if row.status in CLOSED:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This visit has ended.")
    if now - row.started_at > PHONE_VISIT_LIMIT:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="This visit can no longer continue on the phone. Please ask at the desk.",
        )
    token = patient_token(
        settings, clinic_id=principal.clinic_id, session_id=session_id, channel="phone", now=now
    )
    logger.info("session.renew", extra={"session_id": session_id})
    return SessionRenewal(session_token=token, expires_at=now + PATIENT_TOKEN_TTL)
