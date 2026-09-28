"""Anonymous assistance requests: immediate nurse handoff, without invented identity or AI."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials

from careflow_api.config import Settings, get_settings
from careflow_api.consent import get_consent, record_consent
from careflow_api.deps import bearer
from careflow_api.events.frames import publish_frame
from careflow_api.handover import claim_proof, mark_handed_over
from careflow_api.intake.runtime import IntakeRuntime
from careflow_api.models import PatientSession
from careflow_api.ratelimit import rate_limit
from careflow_api.rls import clinic_session
from careflow_api.routers.handover import CLOSED, redis_of
from careflow_api.routers.identity import entry_principal
from careflow_api.routers.sessions import SESSION_EXPIRY, patient_token, runtime, utc_now
from careflow_contracts.api_models import CheckinResult, UrgentCheckinRequest, UrgentHelpRequest
from careflow_contracts.generated.ehr_fetch_schema import EhrStatus
from careflow_contracts.generated.structured_facts_schema import FactKey, Language, Turn, TurnRole
from careflow_contracts.ids import stable_fact_id
from careflow_intake.facts import FactEntry
from careflow_intake.feels_worse import CHEST, FAST
from careflow_intake.state import IntakeState

router = APIRouter(tags=["checkin"])


def urgent_facts(
    body: UrgentCheckinRequest, now: datetime, session_id: str
) -> tuple[list[Turn], list[FactEntry]]:
    """Preserve selected positives only; neither unknown demographics nor negatives are facts."""
    codes = [item.value for item in body.emergency_symptoms]
    request_text = "Urgent assistance requested"
    prefix = f"{request_text}. Selected symptoms: "
    text = prefix + (", ".join(codes) or "none selected") + "."
    turn = Turn(turn_id="trn_urgent_001", role=TurnRole.patient, text=text, created_at=now)

    def fact(key: FactKey, value: Any, quote: str, start: int) -> FactEntry:
        return FactEntry.model_validate(
            {
                "key": key,
                "fact": {
                    "fact_id": stable_fact_id(session_id, "urgent", key.value),
                    "provenance": "PATIENT_STATED",
                    "recorded_at": now,
                    "source": {
                        "kind": "transcript_span",
                        "turn_id": turn.turn_id,
                        "start": start,
                        "end": start + len(quote),
                        "quote": quote,
                    },
                    "value": value,
                },
            }
        )

    facts = [fact(FactKey.chief_complaint, request_text, request_text, 0)]
    mapping = dict(CHEST | FAST)
    if body.patient_type.value == "child":
        mapping["drowsy_or_confused"] = FactKey.child_danger_hard_to_wake
    facts.extend(
        fact(key, "yes", code, text.index(code, len(prefix)))
        for code, key in mapping.items()
        if code in codes
    )
    return [turn], facts


@router.post(
    "/v1/checkin/urgent",
    response_model=CheckinResult,
    response_model_exclude_none=True,
    dependencies=[Depends(rate_limit("urgent-checkin", limit=15, window_seconds=60))],
)
async def checkin(
    body: UrgentCheckinRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    intake: Annotated[IntakeRuntime, Depends(runtime)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> CheckinResult:
    principal, channel = await entry_principal(body.qr_token, request, credentials, settings)
    if not body.consent.data_use:
        raise HTTPException(422, "Agree to share this help request with the care team.")
    proof = claim_proof(body.claim_key)
    device_id = uuid.UUID(principal.device_id or "")
    session_id = str(uuid.uuid5(device_id, f"urgent:{proof}"))
    key = f"urgent:claim:{session_id}"
    fingerprint = hmac.new(
        body.claim_key.encode(),
        body.model_dump_json(exclude={"qr_token", "claim_key"}).encode(),
        hashlib.sha256,
    ).hexdigest()
    redis = redis_of(request)
    lock = redis.lock(f"{key}:lock", timeout=60, blocking=False)
    if not await lock.acquire():
        raise HTTPException(409, "Your help request is being saved. Try again shortly.")
    try:
        saved = await redis.get(key)
        current = await intake.state(session_id)
        durable = (current or {}).get("registration_fingerprint")
        if any(
            value and not hmac.compare_digest(str(value), fingerprint) for value in (saved, durable)
        ):
            raise HTTPException(409, "This help request was already sent with different details.")
        await redis.set(key, fingerprint, ex=3600)
        now = utc_now()
        language = Language(body.language.value if body.language else "en")
        async with clinic_session(principal.clinic_id) as db:
            row = await db.get(PatientSession, uuid.UUID(session_id))
            if row is None:
                row = PatientSession(
                    id=uuid.UUID(session_id),
                    clinic_id=principal.clinic_id,
                    device_id=device_id,
                    fhir_patient_id=None,
                    language=language.value,
                    status="awaiting_nurse",
                    started_at=now,
                    expires_at=now + SESSION_EXPIRY,
                    patient_display="Unknown",
                )
                db.add(row)
        if row.status in CLOSED:
            raise HTTPException(409, "This visit has ended. Start a new help request.")
        if await get_consent(principal.clinic_id, session_id) is None:
            await record_consent(
                principal.clinic_id,
                session_id,
                body.consent,
                language=row.language,
                events=request.app.state.events,
                now=now,
            )
        if not current:
            help_request = UrgentHelpRequest.model_validate(
                {
                    "requested_at": row.started_at,
                    "patient_type": body.patient_type.value,
                    "emergency_symptoms": body.emergency_symptoms,
                }
            )
            turns, facts = urgent_facts(body, now, session_id)
            state = cast(
                IntakeState,
                {
                    "session_id": session_id,
                    "clinic_id": str(principal.clinic_id),
                    "language": language,
                    "arrived_at": row.started_at,
                    "started_at": row.started_at,
                    "patient_type": body.patient_type.value,
                    "channel": channel,
                    "patient_ref": None,
                    "ehr_status": EhrStatus.unavailable,
                    "ehr_errors": [
                        "Identity not yet established; no hospital history was accessed."
                    ],
                    "flags": {"ehr_unavailable": True, "assistance_requested": True},
                    "facts": facts,
                    "turns": turns,
                    "turn_count": 0,
                    "conversation_complete": True,
                    "pending_question": None,
                    "urgent_help": help_request.model_dump(mode="json"),
                    "registration_fingerprint": fingerprint,
                    "node_trace": ["urgent_assistance"],
                },
            )
            await intake.start_reception(state)
        elif not current.get("nurse_decision"):
            # Recover a process failure between saving initial facts and reaching nurse review.
            await intake.start_reception(current)
        # This staff-only frame refreshes worklists even when no clinical rule applies.
        # Publishing on a retry is harmless: the durable visit and consent remain singular.
        await publish_frame(
            redis,
            frame_type="patient.help_requested",
            clinic_id=str(principal.clinic_id),
            session_id=session_id,
            data={"session_id": session_id, "requested_at": row.started_at.isoformat()},
            now=now,
            stream=settings.event_stream,
        )
        if channel == "phone":
            await mark_handed_over(redis, session_id, proof)
        return CheckinResult(
            session_id=session_id,
            session_token=patient_token(
                settings, clinic_id=principal.clinic_id, session_id=session_id, channel=channel
            ),
            patient_display="Unknown",
            language=Language(row.language),
        )
    finally:
        await lock.release()
