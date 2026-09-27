"""The QR hand-over (M2-06): the kiosk shows a one-time code, the phone claims the session.

`POST /v1/sessions/{id}/qr` (the kiosk's device token, or the session's kiosk-channel patient
token) issues a 120-second token for one session. `POST /v1/checkin/qr/claim` needs no login and
is rate-limited per IP: a valid, unspent token for an open session returns a phone-channel patient
token and the question the intake is waiting on. The claim marks the session handed over (the
kiosk's tokens are refused from then on), sets the graph's `channel` to phone, and sends the kiosk
the `session.handed_over` frame so it can forget the patient.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select

from careflow_api.config import Settings, get_settings
from careflow_api.deps import Principal, require_roles
from careflow_api.events.frames import SESSION_HANDED_OVER, publish_frame
from careflow_api.handover import (
    InvalidHandover,
    handed_over,
    issue,
    mark_handed_over,
    spend,
    verify,
)
from careflow_api.intake.runtime import IntakeRuntime
from careflow_api.models import PatientSession
from careflow_api.ratelimit import rate_limit
from careflow_api.rls import clinic_session
from careflow_api.routers.sessions import (
    own_session,
    patient_token,
    pending_question,
    runtime,
    session_row,
    touch,
    utc_now,
)
from careflow_contracts.api_models import CheckinResult, QrClaimRequest, QrToken
from careflow_contracts.generated.structured_facts_schema import Language

logger = logging.getLogger(__name__)
router = APIRouter(tags=["checkin"])

CLOSED: Final = frozenset({"closed", "no_show"})
CLAIMS_PER_MINUTE: Final = 10


def signing_key(settings: Settings) -> str:
    if settings.handover_signing_key is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="The QR hand-over is not configured."
        )
    return settings.handover_signing_key.get_secret_value()


def redis_of(request: Request):  # noqa: ANN201 - the app's async Redis client
    return request.app.state.redis


@router.post("/v1/sessions/{session_id}/qr", response_model=QrToken)
async def issue_qr(
    session_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_roles("kiosk_device", "patient_session"))],
    settings: Annotated[Settings, Depends(get_settings)],
) -> QrToken:
    """A one-time, 120-second hand-over code for this session; only the kiosk may ask."""
    if principal.role == "patient_session":
        own_session(principal, session_id)
        if principal.channel == "phone":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only the kiosk can hand over.")
    row = await session_row(principal, session_id)
    if principal.role == "kiosk_device" and str(row.device_id) != principal.device_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Another kiosk checked this in.")
    if row.status in CLOSED or await handed_over(redis_of(request), session_id):
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
    closed or already handed-over session."""
    try:
        claims = verify(signing_key(settings), body.qr_token)
    except InvalidHandover as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="This code is not valid.") from exc
    redis = redis_of(request)
    if not await spend(redis, claims.jti):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This code was already used.")
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
    if not await mark_handed_over(redis, claims.session_id):
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
    logger.info("checkin.qr_claim", extra={"session_id": claims.session_id})
    return CheckinResult(
        session_id=claims.session_id,
        session_token=token,
        patient_display=row.patient_display or "Unknown",
        language=Language(row.language),
        first_question=question,
    )
