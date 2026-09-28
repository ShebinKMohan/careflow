"""Demo hospital registration with explicit structured answers and no LLM on submission."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select

from careflow_api.config import Settings, get_settings
from careflow_api.consent import get_consent, record_consent
from careflow_api.deps import Principal, bearer, get_principal, require_roles
from careflow_api.handover import claim_proof, mark_handed_over
from careflow_api.hospital import fhir_endpoint
from careflow_api.intake.runtime import IntakeRuntime
from careflow_api.models import Device, PatientSession
from careflow_api.ratelimit import rate_limit
from careflow_api.rls import clinic_session
from careflow_api.routers.handover import CLOSED, redis_of, signing_key
from careflow_api.routers.sessions import (
    SESSION_EXPIRY,
    own_session,
    patient_token,
    runtime,
    session_row,
    utc_now,
)
from careflow_api.routers.start_checkin import verify_entry
from careflow_contracts.api_models import (
    AdaptiveCheckinRequest,
    CheckinResult,
    ReceptionCheckinRequest,
    ReceptionContext,
    ReceptionDirectory,
)
from careflow_contracts.generated.ehr_fetch_schema import EhrStatus
from careflow_contracts.generated.structured_facts_schema import FactKey, Language, Turn, TurnRole
from careflow_contracts.ids import stable_fact_id
from careflow_intake.facts import FactEntry
from careflow_intake.feels_worse import CHEST, FAST
from careflow_intake.state import IntakeState
from careflow_integration.fhir.client import FhirClient, FhirError

router = APIRouter(tags=["checkin"])
NOTICE = (
    "Example hospital and synthetic doctor rota. Your selection is a preference; "
    "no appointment is reserved. The care team confirms your destination."
)
_DEPARTMENTS = [
    (
        "general_medicine",
        "General medicine",
        "General consultations and follow-up visits",
        "Dr Maya Patel",
    ),
    ("cardiology", "Cardiology", "Heart and circulation consultations", "Dr Daniel Reed"),
    ("paediatrics", "Paediatrics", "Care for children and young people", "Dr Aisha Rahman"),
    ("orthopaedics", "Orthopaedics", "Bones, joints and movement", "Dr Thomas Lee"),
    ("dermatology", "Dermatology", "Skin consultations", "Dr Olivia Grant"),
    ("ent", "Ear, nose and throat", "Ear, nose and throat consultations", "Dr Samir Khan"),
    ("ophthalmology", "Eye care", "Eye and vision consultations", "Dr Elena Silva"),
    ("womens_health", "Women's health", "Gynaecology and women's health", "Dr Hannah Brooks"),
    (
        "emergency",
        "Emergency care",
        "Tell a member of staff immediately if you need urgent help",
        "Dr James Wilson",
    ),
    (
        "diagnostics",
        "Tests and diagnostics",
        "Planned tests and results enquiries",
        "Dr Priya Shah",
    ),
    ("unsure", "Help me choose", "Reception will help you find the appropriate service", None),
    ("dental", "Dental care", "Dental and oral health consultations", "Dr Adam Chen"),
]
DIRECTORY = ReceptionDirectory.model_validate(
    {
        "hospital_name": "Northstar General Hospital",
        "demo": True,
        "notice": NOTICE,
        "departments": [
            {
                "id": key,
                "name": name,
                "description": description,
                "doctors": [
                    {"id": f"demo_{key}_01", "name": doctor, "availability": "demo_available"}
                ]
                if doctor
                else [],
            }
            for key, name, description, doctor in _DEPARTMENTS
        ],
    }
)


@router.get("/v1/reception/directory", response_model=ReceptionDirectory)
async def directory() -> ReceptionDirectory:
    return DIRECTORY


def registration_facts(
    body: ReceptionCheckinRequest | AdaptiveCheckinRequest, now: Any, session_id: str
) -> tuple[list[Turn], list[FactEntry]]:
    patient = body.patient
    codes = [item.value for item in body.emergency_symptoms]
    age_text, sex_text = str(patient.age_years), patient.sex.value
    reason = body.reason or ""
    sex_prefix = f"Age: {age_text}. Sex: "
    reason_prefix = f"{sex_prefix}{sex_text}. Visit reason: "
    symptom_prefix = f"{reason_prefix}{reason}. Selected symptoms: "
    symptom_text = ", ".join(codes) or "none selected"
    text = f"{symptom_prefix}{symptom_text}."
    turn = Turn(turn_id="trn_reception_001", role=TurnRole.patient, text=text, created_at=now)

    def fact(key: FactKey, value: Any, quote: str, start: int) -> FactEntry:
        return FactEntry.model_validate(
            {
                "key": key,
                "fact": {
                    "fact_id": stable_fact_id(session_id, "reception", key.value),
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

    facts = [
        fact(FactKey.patient_age_years, patient.age_years, age_text, len("Age: ")),
        fact(
            FactKey.patient_sex,
            {
                "system": "http://hl7.org/fhir/administrative-gender",
                "code": patient.sex.value,
                "display": patient.sex.value,
            },
            sex_text,
            len(sex_prefix),
        ),
    ]
    if reason:
        facts.append(fact(FactKey.chief_complaint, reason, reason, len(reason_prefix)))
    facts.extend(
        fact(key, "yes", code, len(symptom_prefix) + symptom_text.index(code))
        for code, key in (CHEST | FAST).items()
        if code in codes
    )
    if patient.age_years < 16 and "drowsy_or_confused" in codes:
        code = "drowsy_or_confused"
        facts.append(
            fact(
                FactKey.child_danger_hard_to_wake,
                "yes",
                code,
                len(symptom_prefix) + symptom_text.index(code),
            )
        )
    return [turn], facts


@router.post(
    "/v1/reception/checkin",
    response_model=CheckinResult,
    response_model_exclude_none=True,
    dependencies=[Depends(rate_limit("reception-checkin", limit=15, window_seconds=60))],
)
async def checkin(
    body: ReceptionCheckinRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    intake: Annotated[IntakeRuntime, Depends(runtime)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> CheckinResult:
    if not body.consent.data_use:
        raise HTTPException(
            422, "Please agree to share your registration with the care team, or ask at reception."
        )
    if not body.patient.name.strip() or not body.reason.strip():
        raise HTTPException(422, "Please enter the patient's name and reason for the visit.")
    department = next(
        (item for item in DIRECTORY.departments if item.id == body.department_id), None
    )
    if department is None:
        raise HTTPException(422, "Please choose a department from this hospital's directory.")
    doctor = next((item for item in department.doctors if item.id == body.doctor_id), None)
    if body.doctor_id and doctor is None:
        raise HTTPException(
            422, "Please choose a doctor from this department, or any available clinician."
        )
    if body.qr_token:
        entry = verify_entry(signing_key(settings), body.qr_token)
        principal = Principal(
            subject=entry["device_id"],
            role="kiosk_device",
            clinic_id=uuid.UUID(entry["clinic_id"]),
            token_id=entry["jti"],
            device_id=entry["device_id"],
        )
        channel = "phone"
    else:
        principal = await get_principal(request, credentials, settings)
        if principal.role != "kiosk_device":
            raise HTTPException(
                403, "Registration requires an authorized kiosk or welcome QR code."
            )
        channel = "kiosk"
    async with clinic_session(principal.clinic_id) as db:
        device = (
            await db.execute(
                select(Device).where(Device.id == uuid.UUID(principal.device_id or ""))
            )
        ).scalar_one_or_none()
    if device is None or not device.is_active or device.kind != "kiosk":
        raise HTTPException(401, "This kiosk is no longer available.")
    proof = claim_proof(body.claim_key)
    session_id = str(uuid.uuid5(device.id, f"reception:{proof}"))
    key = f"reception:claim:{session_id}"
    fingerprint = hmac.new(
        body.claim_key.encode(),
        body.model_dump_json(exclude={"qr_token", "claim_key"}).encode(),
        hashlib.sha256,
    ).hexdigest()
    redis = redis_of(request)
    lock = redis.lock(f"{key}:lock", timeout=120, blocking=False)
    if not await lock.acquire():
        raise HTTPException(409, "Your registration is being prepared. Try again shortly.")
    try:
        saved = await redis.get(key)
        if saved and not hmac.compare_digest(str(saved), fingerprint):
            raise HTTPException(
                409, "This registration was already submitted with different details."
            )
        await redis.set(key, fingerprint, ex=3600)
        now = utc_now()
        language = Language(body.language.value if body.language else "en")
        context = ReceptionContext(
            patient=body.patient,
            department_id=department.id,
            department_name=department.name,
            doctor_id=doctor.id if doctor else None,
            doctor_name=doctor.name if doctor else None,
            visit_purpose=body.visit_purpose,
            reason=body.reason,
            emergency_symptoms=body.emergency_symptoms,
            registered_at=now,
            availability_notice=NOTICE,
        )
        async with clinic_session(principal.clinic_id) as db:
            row = await db.get(PatientSession, uuid.UUID(session_id))
        if row is None:
            identifier = f"urn:careflow:reception|{session_id}"
            resource: dict[str, Any] = {
                "resourceType": "Patient",
                "active": True,
                "identifier": [{"system": "urn:careflow:reception", "value": session_id}],
                "name": [{"text": body.patient.name.strip()}],
                "gender": body.patient.sex.value,
                "extension": [
                    {
                        "url": "https://careflow.example/fhir/StructureDefinition/patient-reported-age-years",
                        "valueInteger": body.patient.age_years,
                    }
                ],
            }
            if body.patient.phone:
                resource["telecom"] = [
                    {"system": "phone", "value": body.patient.phone, "use": "mobile"}
                ]
            try:
                async with FhirClient(fhir_endpoint()) as client:
                    created = await client.create(
                        resource, if_none_exist=f"identifier={identifier}"
                    )
            except FhirError as exc:
                raise HTTPException(
                    503,
                    "The patient record service is temporarily unavailable. "
                    "Your details are still on this screen; try again.",
                ) from exc
            initials = " ".join(f"{part[0].upper()}." for part in body.patient.name.split()[:2])[:8]
            row = PatientSession(
                id=uuid.UUID(session_id),
                clinic_id=principal.clinic_id,
                device_id=device.id,
                fhir_patient_id=created.resource_id,
                language=language.value,
                status="awaiting_nurse",
                started_at=now,
                expires_at=now + SESSION_EXPIRY,
                patient_display=initials,
            )
            async with clinic_session(principal.clinic_id) as db:
                db.add(row)
        if row.status in CLOSED:
            raise HTTPException(409, "This visit has ended. Start a new registration.")
        if await get_consent(principal.clinic_id, session_id) is None:
            await record_consent(
                principal.clinic_id,
                session_id,
                body.consent,
                language=row.language,
                events=request.app.state.events,
                now=now,
            )
        turns, facts = registration_facts(body, now, session_id)
        state = cast(
            IntakeState,
            {
                "session_id": session_id,
                "clinic_id": str(principal.clinic_id),
                "language": language,
                "arrived_at": row.started_at,
                "started_at": row.started_at,
                "patient_type": "child" if body.patient.age_years < 16 else "adult",
                "channel": channel,
                "patient_ref": f"Patient/{row.fhir_patient_id}",
                "ehr_status": EhrStatus.partial,
                "ehr_errors": [
                    "New patient registration; previous clinical history has not been verified."
                ],
                "flags": {"ehr_unavailable": True},
                "facts": facts,
                "turns": turns,
                "turn_count": 0,
                "conversation_complete": True,
                "pending_question": None,
                "reception_context": context.model_dump(mode="json", exclude_none=True),
                "node_trace": ["reception_registration"],
            },
        )
        await intake.start_reception(state)
        if channel == "phone":
            await mark_handed_over(redis, session_id, proof)
        return CheckinResult(
            session_id=session_id,
            session_token=patient_token(
                settings, clinic_id=principal.clinic_id, session_id=session_id, channel=channel
            ),
            patient_display=row.patient_display or "Unknown",
            language=Language(row.language),
        )
    finally:
        await lock.release()


@router.get(
    "/v1/sessions/{session_id}/reception",
    response_model=ReceptionContext,
    response_model_exclude_none=True,
)
async def reception_context(
    session_id: str,
    principal: Annotated[
        Principal, Depends(require_roles("patient_session", "nurse", "doctor", "admin"))
    ],
    intake: Annotated[IntakeRuntime, Depends(runtime)],
) -> ReceptionContext:
    own_session(principal, session_id)
    await session_row(principal, session_id)
    state = await intake.state(session_id)
    context = (state or {}).get("reception_context")
    if context is None:
        raise HTTPException(404, "This visit has no reception registration.")
    return ReceptionContext.model_validate(context)
