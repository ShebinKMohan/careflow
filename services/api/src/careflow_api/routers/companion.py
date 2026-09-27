"""The patient's phone after the QR hand-over (M2-07): the waiting room and "I feel worse".

Phone-channel tokens only (`require_phone_session`): the session is the token's own. "I feel
worse" records the press and its `patient.feels_worse` event first, then maps the ticked answers
to facts and re-runs the safety rules (new rule ids go out as `alert.raised`), then writes a FHIR
`Flag` in the background. A failure after the first step still answers 202: the nurse was told.
Every press counts; repeats are never dropped.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request, status

from careflow_api.config import Settings, get_settings
from careflow_api.deps import Principal, require_phone_session
from careflow_api.feels_worse import record_press
from careflow_api.intake.runtime import IntakeRuntime
from careflow_api.queue.service import QueueService
from careflow_api.rls import clinic_session
from careflow_api.routers.sessions import session_row, utc_now
from careflow_contracts.api_models import (
    CompanionStatus,
    FeelsWorseAck,
    FeelsWorseRequest,
    PatientType,
)
from careflow_intake.feels_worse import answer_codes
from careflow_integration.fhir.client import FhirClient, FhirError
from careflow_integration.fhir.writeback.feels_worse import feels_worse_flag, write_feels_worse_flag

logger = logging.getLogger(__name__)
router = APIRouter(tags=["companion"])

Phone = Annotated[Principal, Depends(require_phone_session)]


def session_of(principal: Principal) -> str:
    if principal.session_id is None:  # a phone token always names its session
        raise RuntimeError("a phone token without a session")
    return principal.session_id


@router.get("/v1/companion/status", response_model=CompanionStatus)
async def companion_status(principal: Phone, request: Request) -> CompanionStatus:
    """The patient's token, room, place in the queue and wait estimate; never a level."""
    session_id = session_of(principal)
    row = await session_row(principal, session_id)
    queue: QueueService = request.app.state.queue
    async with clinic_session(principal.clinic_id) as db:
        status = await queue.companion_status(db, session_id=session_id, session_state=row.status)
    intake: IntakeRuntime | None = getattr(request.app.state, "intake", None)
    state = await intake.state(session_id) if intake is not None else None
    patient_type = state.get("patient_type", "adult") if state else "adult"
    return status.model_copy(update={"patient_type": PatientType(patient_type)})


async def _write_flag(
    fhir_base_url: str, *, session_id: str, patient_ref: str, press: int, codes: list[str], now
) -> None:  # noqa: ANN001 - a datetime
    flag = feels_worse_flag(
        session_id=session_id,
        patient_ref=patient_ref,
        press_number=press,
        answer_codes=codes,
        reported_at=now,
    )
    try:
        async with FhirClient(fhir_base_url) as client:
            await write_feels_worse_flag(client, flag)
    except FhirError:
        logger.warning("feels_worse.flag_failed", extra={"session_id": session_id})


@router.post(
    "/v1/companion/feels-worse",
    response_model=FeelsWorseAck,
    status_code=status.HTTP_202_ACCEPTED,
)
async def feels_worse(
    body: FeelsWorseRequest,
    principal: Phone,
    request: Request,
    background: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
) -> FeelsWorseAck:
    """Safety feature: the nurse hears of every press first; then the rules run again."""
    session_id = session_of(principal)
    await session_row(principal, session_id)
    now = utc_now()
    answers = body.answers
    codes = answer_codes(
        [item.value for item in answers.chest_or_breathing],
        [item.value for item in answers.face_arm_speech],
        answers.drowsy_or_confused.value,
    )
    flag_id, press = await record_press(
        principal.clinic_id,
        session_id,
        codes=codes,
        note=body.note,
        events=request.app.state.events,
        now=now,
    )
    intake: IntakeRuntime | None = getattr(request.app.state, "intake", None)
    try:
        if intake is not None:
            state = await intake.report_feels_worse(session_id, codes=codes, now=now)
            patient_ref = state.get("patient_ref")
            if patient_ref:
                background.add_task(
                    _write_flag,
                    settings.fhir_base_url,
                    session_id=session_id,
                    patient_ref=patient_ref,
                    press=press,
                    codes=codes,
                    now=now,
                )
    except Exception:  # the event already went out: the nurse was told
        logger.exception("feels_worse.follow_up_failed", extra={"session_id": session_id})
    return FeelsWorseAck(flag_id=flag_id, received_at=now, press_count=press)
