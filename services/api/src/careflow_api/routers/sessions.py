"""Patient-side routes (M1-12): the silent-keypad check-in, the intake turns, the patient's status,
device vitals, and the session's facts and triage view for staff.

The intake graph (M1-05) runs behind them in `IntakeRuntime`. Keypad digits, turn text and tokens
are never logged. The patient never gets a level or an alert (safety rules 2 and 3).
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from collections.abc import Mapping
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, update

from careflow_api.config import Settings, get_settings
from careflow_api.consent import get_consent
from careflow_api.deps import Principal, require_roles
from careflow_api.devices.gateway import latest_vitals
from careflow_api.hospital import fhir_endpoint, hospital_adapter
from careflow_api.intake.runtime import (
    IntakeRevisionConflict,
    IntakeRuntime,
    PatientAlreadySet,
    intake_revision,
)
from careflow_api.intake.views import patient_status, triage_view
from careflow_api.models import PatientSession
from careflow_api.ratelimit import failure_limit
from careflow_api.rls import clinic_session
from careflow_api.security import create_access_token
from careflow_contracts.api_models import (
    CheckinResult,
    DeviceVitalsReading,
    IdTapCheckinRequest,
    IntakeQuestion,
    KeypadCheckinRequest,
    PatientDetails,
    PatientDetailsRequest,
    PatientType,
    SessionStatus,
    TriageView,
    TurnRequest,
    TurnResponse,
    VitalsAccepted,
)
from careflow_contracts.generated.ehr_fetch_schema import EhrFetch, EhrStatus, PatientIdentifier
from careflow_contracts.generated.structured_facts_schema import (
    Language,
    StructuredFacts,
    TurnRole,
)
from careflow_contracts.ids import stable_fact_id
from careflow_intake.facts import FactEntry
from careflow_intake.graph import NotWaiting, pending
from careflow_intake.nodes.conversation import InvalidAnswer
from careflow_intake.state import structured_facts
from careflow_integration.fhir.client import FhirClient, FhirError

logger = logging.getLogger("careflow_api.sessions")

router = APIRouter(tags=["sessions"])

KEYPAD_LIMIT: Final = 5
KEYPAD_WINDOW_SECONDS: Final = 300
PATIENT_TOKEN_TTL: Final = dt.timedelta(minutes=30)
SESSION_EXPIRY: Final = dt.timedelta(hours=2)
UNKNOWN_DIGITS: Final = "We could not check you in with that number. Please ask at the desk."
UNKNOWN_CARD: Final = "We could not find your record with this card. Please use the keypad."
AMBIGUOUS_CARD: Final = "We could not check you in with this card. Please go to the reception desk."
VOICE_NOT_CONSENTED: Final = "Voice was not consented to for this session."
DONE_TEXT: Final = "Thank you. A nurse will see you shortly. Please stay here."
DEVICE_FIELDS: Final = {
    "heart_rate": ("vitals.heart_rate", "/min"),
    "spo2": ("vitals.spo2", "%"),
    "temperature_c": ("vitals.temperature_c", "Cel"),
    "systolic_bp": ("vitals.systolic_bp", "mm[Hg]"),
    "diastolic_bp": ("vitals.diastolic_bp", "mm[Hg]"),
}


def runtime(request: Request) -> IntakeRuntime:
    found: IntakeRuntime | None = getattr(request.app.state, "intake", None)
    if found is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="The intake is starting.")
    return found


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def initials(patient: dict[str, Any]) -> str:
    """ "M. S." from a FHIR Patient's first name; never more than initials."""
    names = patient.get("name") or []
    if not names:
        return "Unknown"
    name = names[0]
    given = (name.get("given") or [""])[0][:1]
    family = (name.get("family") or "")[:1]
    parts = [f"{letter.upper()}." for letter in (given, family) if letter]
    return " ".join(parts) or "Unknown"


async def patient_display(patient_ref: str | None) -> str:
    if patient_ref is None:
        return "Unknown"
    try:
        async with FhirClient(fhir_endpoint()) as client:
            kind, rid = patient_ref.split("/", 1)
            return initials(await client.read(kind, rid))
    except (FhirError, ValueError):
        return "Unknown"


def question_of(result: dict[str, Any]) -> IntakeQuestion | None:
    """The next question from the graph's pause, or None when it is not asking the patient."""
    return question_from(pending(result))


def question_from(value: dict[str, Any] | None) -> IntakeQuestion | None:
    """The question in a pause's value (`IntakeRuntime.waiting_for`), or None."""
    if not value or value.get("kind") != "patient_input":
        return None
    return intake_question(value["question"])


def intake_question(q: Mapping[str, Any]) -> IntakeQuestion:
    """The contract question; a side question's reply comes as the notice, with its kind and
    citations (M2-10)."""
    return IntakeQuestion(
        question_id=q["question_id"],
        text=q["text"],
        key=q.get("key"),
        notice=q.get("notice"),
        notice_kind=q.get("notice_kind"),
        citations=q.get("citations") or None,
    )


def reply_text(question: IntakeQuestion | None, state: Mapping[str, Any]) -> str:
    """What the kiosk says: the notice (a side question's reply, M2-10), a blank line and the
    question; or the done message, after any reply still waiting to be said."""
    if question is None:
        prefix = state.get("reply_prefix")
        return f"{prefix}\n\n{DONE_TEXT}" if prefix else DONE_TEXT
    return f"{question.notice}\n\n{question.text}" if question.notice else question.text


def pending_question(state: Mapping[str, Any] | None) -> IntakeQuestion | None:
    """The question the intake is waiting on, from the graph's state (it survives any state update,
    unlike the interrupt's value), or None."""
    raw = (state or {}).get("pending_question")
    if raw is None:
        return None
    q = raw.model_dump(mode="json") if hasattr(raw, "model_dump") else dict(raw)
    return intake_question(q)


def patient_token(
    settings: Settings,
    *,
    clinic_id: uuid.UUID,
    session_id: str,
    channel: str,
    now: dt.datetime | None = None,
) -> str:
    """A 30-minute patient token for one session (from `now`), on the kiosk or (after the QR
    hand-over, M2-06) the phone."""
    token, _ = create_access_token(
        secret=settings.jwt_secret.get_secret_value(),
        subject=f"session:{session_id}",
        role="patient_session",
        clinic_id=clinic_id,
        extra={"session_id": session_id, "scope": f"session:{session_id}", "channel": channel},
        now=now,
        ttl=PATIENT_TOKEN_TTL,
    )
    return token


async def session_row(principal: Principal, session_id: str) -> PatientSession:
    """The session of the caller's clinic (RLS), or 404."""
    try:
        key = uuid.UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such session.") from exc
    async with clinic_session(principal.clinic_id) as db:
        row = (
            await db.execute(select(PatientSession).where(PatientSession.id == key))
        ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such session.")
    return row


def own_session(principal: Principal, session_id: str) -> None:
    """A patient token only ever reaches its own session."""
    if principal.role == "patient_session" and principal.session_id != session_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This token is for another session.")


async def touch(clinic_id: uuid.UUID, session_id: str, **values: Any) -> None:
    async with clinic_session(clinic_id) as db:
        await db.execute(
            update(PatientSession)
            .where(PatientSession.id == uuid.UUID(session_id))
            .values(last_activity_at=utc_now(), **values)
        )


@router.post("/v1/checkin/keypad", response_model=CheckinResult, response_model_exclude_none=True)
async def keypad_checkin(
    body: KeypadCheckinRequest,
    principal: Annotated[Principal, Depends(require_roles("kiosk_device"))],
    intake: Annotated[IntakeRuntime, Depends(runtime)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CheckinResult:
    """Digits -> the EHR patient -> a new session with its intake started, and a 30-minute
    patient token. Unknown digits get a generic 404; after 5 of them in 5 minutes the kiosk gets
    429 (guessing protection: successful check-ins are not counted)."""
    if body.clinic_id != str(principal.clinic_id) or body.kiosk_id != principal.device_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This kiosk may not check in here.")
    allowed, record_failure = failure_limit(
        "keypad", body.kiosk_id, limit=KEYPAD_LIMIT, window_seconds=KEYPAD_WINDOW_SECONDS
    )
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Please wait a few minutes or ask at the desk.",
        )
    adapter = hospital_adapter()
    identifier = PatientIdentifier(system=adapter.identifier_systems["keypad"], value=body.digits)
    fetch = await intake.ports.ehr.fetch_context(identifier, body.clinic_id)
    if fetch.status is EhrStatus.not_found:
        record_failure()
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=UNKNOWN_DIGITS)
    result = await open_session(
        principal=principal,
        intake=intake,
        settings=settings,
        identifier=identifier,
        fetch=fetch,
        kiosk_id=body.kiosk_id,
        language=body.language,
    )
    logger.info(
        "checkin.keypad", extra={"session_id": result.session_id, "ehr": fetch.status.value}
    )
    return result


async def open_session(
    *,
    principal: Principal,
    intake: IntakeRuntime,
    settings: Settings,
    identifier: PatientIdentifier,
    fetch: EhrFetch,
    kiosk_id: str,
    language: Language | None,
) -> CheckinResult:
    """The session row, the intake started and a 30-minute patient token: the same for every
    check-in path (keypad, ID tap)."""
    now = utc_now()
    session_id = uuid.uuid4()
    lang = Language(language.value) if language is not None else Language.en
    display = await patient_display(fetch.patient_ref)
    fhir_id = fetch.patient_ref.split("/", 1)[1] if fetch.patient_ref else None
    async with clinic_session(principal.clinic_id) as db:
        db.add(
            PatientSession(
                id=session_id,
                clinic_id=principal.clinic_id,
                device_id=uuid.UUID(kiosk_id),
                fhir_patient_id=fhir_id,
                language=lang.value,
                status="intake",
                started_at=now,
                expires_at=now + SESSION_EXPIRY,
                patient_display=display,
            )
        )
    result = await intake.start(
        session_id=str(session_id),
        clinic_id=str(principal.clinic_id),
        arrived_at=now,
        language=lang,
        extra={"patient_identifier": identifier},
    )
    token = patient_token(
        settings, clinic_id=principal.clinic_id, session_id=str(session_id), channel="kiosk"
    )
    return CheckinResult(
        session_id=str(session_id),
        session_token=token,
        patient_display=display,
        language=lang,
        first_question=question_of(result),
    )


def masked_card(id_number: str) -> str:
    """784-****-*******-C: a mock ID in logs keeps only its check digit."""
    return f"784-****-*******-{id_number[-1:]}"


@router.post("/v1/checkin/id-tap", response_model=CheckinResult, response_model_exclude_none=True)
async def id_tap_checkin(
    body: IdTapCheckinRequest,
    principal: Annotated[Principal, Depends(require_roles("kiosk_device"))],
    intake: Annotated[IntakeRuntime, Depends(runtime)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CheckinResult:
    """A simulated ID-card tap (M2-05) -> the FHIR Patient with that synthetic identifier -> a
    session exactly as the keypad path. No match: 404 (use the keypad); more than one: 409
    (reception). Failed taps count against the kiosk like keypad guesses; the id number is only
    ever logged masked."""
    if body.clinic_id != str(principal.clinic_id) or body.kiosk_id != principal.device_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This kiosk may not check in here.")
    allowed, record_failure = failure_limit(
        "id-tap", body.kiosk_id, limit=KEYPAD_LIMIT, window_seconds=KEYPAD_WINDOW_SECONDS
    )
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Please wait a few minutes or ask at the desk.",
        )
    card = body.mock_card_payload.id_number
    system = hospital_adapter().identifier_systems["mock_national_id"]
    identifier = PatientIdentifier(system=system, value=card)
    fetch = await intake.ports.ehr.fetch_context(identifier, body.clinic_id)
    if fetch.status is EhrStatus.not_found:
        record_failure()
        logger.info("checkin.id_tap.not_found", extra={"card": masked_card(card)})
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=UNKNOWN_CARD)
    if fetch.status is EhrStatus.ambiguous:
        logger.warning("checkin.id_tap.ambiguous", extra={"card": masked_card(card)})
        raise HTTPException(status.HTTP_409_CONFLICT, detail=AMBIGUOUS_CARD)
    result = await open_session(
        principal=principal,
        intake=intake,
        settings=settings,
        identifier=identifier,
        fetch=fetch,
        kiosk_id=body.kiosk_id,
        language=body.language,
    )
    logger.info(
        "checkin.id_tap",
        extra={
            "session_id": result.session_id,
            "card": masked_card(card),
            "ehr": fetch.status.value,
        },
    )
    return result


@router.post(
    "/v1/sessions/{session_id}/turns",
    response_model=TurnResponse,
    response_model_exclude_none=False,
)
async def post_turn(
    session_id: str,
    body: TurnRequest,
    principal: Annotated[Principal, Depends(require_roles("patient_session"))],
    intake: Annotated[IntakeRuntime, Depends(runtime)],
) -> TurnResponse:
    """The patient's answer; the reply is the next question, or done."""
    own_session(principal, session_id)
    await session_row(principal, session_id)
    if body.input_mode.value == "voice":  # the microphone needs the session's consent (M2-05)
        consent = await get_consent(principal.clinic_id, session_id)
        if consent is None or not consent.voice:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=VOICE_NOT_CONSENTED)
    try:
        result = await intake.submit_turn(
            session_id,
            body.text,
            body.input_mode.value,
            expected_intake_revision=body.expected_intake_revision,
        )
    except IntakeRevisionConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except NotWaiting as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="This session is not waiting for an answer."
        ) from exc
    except InvalidAnswer as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    await touch(principal.clinic_id, session_id)
    turns = result.get("turns", [])
    said = next(t for t in reversed(turns) if t.role is TurnRole.patient)
    question = question_of(result)
    if question is None:
        await touch(principal.clinic_id, session_id, status="awaiting_nurse")
    return TurnResponse(
        turn_id=said.turn_id,
        intake_revision=intake_revision(result),
        assistant_text=reply_text(question, result),
        done=question is None,
        next_question=question,
    )


@router.post(
    "/v1/sessions/{session_id}/patient",
    response_model=PatientDetails,
    response_model_exclude_none=True,
)
async def set_patient_details(
    session_id: str,
    body: PatientDetailsRequest,
    principal: Annotated[Principal, Depends(require_roles("patient_session"))],
    intake: Annotated[IntakeRuntime, Depends(runtime)],
) -> PatientDetails:
    """The kiosk's "Who is the patient?" answer (M2-04). A child's age is required and becomes
    PATIENT_STATED age facts; the intake then asks the child question bank."""
    own_session(principal, session_id)
    await session_row(principal, session_id)
    child = body.patient_type is PatientType.child
    if child and body.age_years is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="A child's age is required."
        )
    if not child and (body.age_years is not None or body.age_months is not None):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="An adult's age comes from the record."
        )
    try:
        await intake.set_patient(
            session_id,
            patient_type=body.patient_type.value,
            age_years=body.age_years,
            age_months=body.age_months,
        )
    except NotWaiting as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Session not found.") from exc
    except PatientAlreadySet as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Who the patient is was already answered."
        ) from exc
    await touch(principal.clinic_id, session_id)
    return PatientDetails(
        session_id=session_id,
        patient_type=body.patient_type,
        age_years=body.age_years,
        age_months=body.age_months,
    )


@router.get(
    "/v1/sessions/{session_id}/status",
    response_model=SessionStatus,
    response_model_exclude_none=True,
)
async def get_status(
    session_id: str,
    principal: Annotated[Principal, Depends(require_roles("patient_session", "kiosk_device"))],
    intake: Annotated[IntakeRuntime, Depends(runtime)],
    request: Request,
) -> SessionStatus:
    """The state, token, room and wait. Never a level or an alert."""
    own_session(principal, session_id)
    await session_row(principal, session_id)
    # An answer can advance the graph while a phone reloads. Read the state and its pending
    # question in the same session lock so the response cannot mix two different turns.
    async with intake.serialized(session_id):
        state = await intake.state(session_id)
        waiting, pause = await intake.waiting_for(session_id)
        # QR hand-over updates the checkpoint's channel, which can clear the interrupt payload.
        # Its pending question remains in state; use it only while the graph waits for an answer.
        current_question = (
            question_from(pause) or pending_question(state) if waiting == "await_answer" else None
        )
    async with clinic_session(principal.clinic_id) as db:
        token = await request.app.state.queue.session_token(db, session_id=session_id)
    found = patient_status(
        state,
        waiting=waiting,
        token_code=token.token_code if token else None,
        token_status=token.status.value if token else None,
        room=token.room if token else None,
        wait_estimate_min=None,
        current_question=current_question,
    )
    vitals = latest_vitals.get(session_id)  # the kiosk's vitals panel: numbers only
    return found.model_copy(update={"vitals": vitals}) if vitals is not None else found


@router.get(
    "/v1/sessions/{session_id}/facts",
    response_model=StructuredFacts,
    response_model_exclude_none=True,
)
async def get_facts(
    session_id: str,
    principal: Annotated[Principal, Depends(require_roles("nurse", "doctor"))],
    intake: Annotated[IntakeRuntime, Depends(runtime)],
) -> StructuredFacts:
    await session_row(principal, session_id)
    state = await intake.state(session_id)
    if state is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No intake for this session.")
    return structured_facts(state)


def device_facts(session_id: str, reading: DeviceVitalsReading) -> list[FactEntry]:
    source = {"kind": "device", "device_id": reading.device_id, "reading_id": reading.reading_id}
    entries = []
    for name, (key, unit) in DEVICE_FIELDS.items():
        value = getattr(reading, name)
        if value is None:
            continue
        fact = {
            "fact_id": stable_fact_id(session_id, key, reading.device_id, reading.reading_id),
            "provenance": "DEVICE_MEASURED",
            "source": source,
            "recorded_at": reading.measured_at,
            "value": value,
            "unit": unit,
        }
        entries.append(FactEntry.model_validate({"key": key, "fact": fact}))
    return entries


@router.post("/v1/sessions/{session_id}/vitals", response_model=VitalsAccepted)
async def post_device_vitals(
    session_id: str,
    reading: DeviceVitalsReading,
    principal: Annotated[Principal, Depends(require_roles("kiosk_device"))],
    intake: Annotated[IntakeRuntime, Depends(runtime)],
) -> VitalsAccepted:
    """A device reading becomes DEVICE_MEASURED facts; the safety rules run at once."""
    await session_row(principal, session_id)
    if await intake.state(session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No intake for this session.")
    entries = device_facts(session_id, reading)
    if entries:
        await intake.add_facts_and_evaluate(session_id, entries)
    await touch(principal.clinic_id, session_id)
    return VitalsAccepted(session_id=session_id, fact_ids=[e.fact_id for e in entries])


@router.get(
    "/v1/sessions/{session_id}/triage", response_model=TriageView, response_model_exclude_unset=True
)
async def get_triage(
    session_id: str,
    principal: Annotated[Principal, Depends(require_roles("nurse", "doctor"))],
    intake: Annotated[IntakeRuntime, Depends(runtime)],
) -> TriageView:
    await session_row(principal, session_id)
    state = await intake.state(session_id)
    if state is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No intake for this session.")
    return triage_view(state)


__all__ = ["device_facts", "initials", "question_of", "router", "runtime", "session_row"]
