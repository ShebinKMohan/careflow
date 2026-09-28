"""Consented patient registration starts the real adaptive clinical intake graph.

A verified caregiver is never used as the patient's EHR identity. The reception-only endpoint
remains separate; this route returns the clinical graph's current patient question.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials

from careflow_api.config import Settings, get_settings
from careflow_api.consent import get_consent, record_consent
from careflow_api.deps import bearer
from careflow_api.handover import claim_proof, mark_handed_over
from careflow_api.hospital import fhir_endpoint
from careflow_api.intake.runtime import IntakeRuntime
from careflow_api.models import PatientSession
from careflow_api.ratelimit import rate_limit
from careflow_api.rls import clinic_session
from careflow_api.routers.handover import CLOSED, redis_of
from careflow_api.routers.identity import entry_principal, verified_identity
from careflow_api.routers.reception import DIRECTORY, NOTICE, registration_facts
from careflow_api.routers.sessions import (
    SESSION_EXPIRY,
    patient_token,
    pending_question,
    question_of,
    runtime,
    utc_now,
)
from careflow_contracts.api_models import AdaptiveCheckinRequest, CheckinResult, ReceptionContext
from careflow_contracts.generated.ehr_fetch_schema import EhrFetch, EhrStatus, PatientIdentifier
from careflow_contracts.generated.structured_facts_schema import Language
from careflow_intake.state import IntakeState
from careflow_integration.fhir.client import FhirClient, FhirError

router = APIRouter(tags=["checkin"])


def same_profile(supplied: Any, verified: dict[str, Any]) -> bool:
    return (
        supplied.name.strip().casefold() == verified["name"].strip().casefold()
        and supplied.age_years == verified["age_years"]
        and supplied.sex.value == verified["sex"]
    )


@router.post(
    "/v1/checkin/adaptive",
    response_model=CheckinResult,
    response_model_exclude_none=True,
    dependencies=[Depends(rate_limit("adaptive-checkin", limit=15, window_seconds=60))],
)
async def checkin(
    body: AdaptiveCheckinRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    intake: Annotated[IntakeRuntime, Depends(runtime)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> CheckinResult:
    principal, channel = await entry_principal(body.qr_token, request, credentials, settings)
    if not body.consent.data_use:
        raise HTTPException(
            422,
            (
                "Please agree to share this patient's answers with the care team, "
                "or ask reception for help."
            ),
        )
    if len(body.patient.name.strip()) < 2:
        raise HTTPException(422, "Enter the actual patient's name.")
    visit_for = body.visit_for.value
    if visit_for != "self" and (body.caregiver is None or len(body.caregiver.name.strip()) < 2):
        raise HTTPException(422, "Please enter the name of the person helping the patient.")
    if visit_for == "self" and body.caregiver is not None:
        raise HTTPException(422, "Choose a dependent visit to record a separate caregiver.")
    if visit_for == "child" and body.patient.age_years >= 16:
        raise HTTPException(422, "The child pathway is for patients under sixteen.")
    if visit_for == "elder" and body.patient.age_years < 16:
        raise HTTPException(422, "Choose the child pathway for a patient under sixteen.")
    department = next((d for d in DIRECTORY.departments if d.id == body.department_id), None)
    if department is None:
        raise HTTPException(
            422, "Choose a department from the hospital directory, or leave it undecided."
        )
    doctor = next((d for d in department.doctors if d.id == body.doctor_id), None)
    if body.doctor_id and doctor is None:
        raise HTTPException(422, "This doctor is not listed in the selected department.")
    patient_identity = (
        await verified_identity(
            body.patient_identity_token, principal, body.claim_key, request, settings
        )
        if body.patient_identity_token
        else None
    )
    caregiver_identity = (
        await verified_identity(
            body.caregiver.identity_token, principal, body.claim_key, request, settings
        )
        if body.caregiver and body.caregiver.identity_token
        else None
    )
    if patient_identity and not same_profile(body.patient, patient_identity["patient"]):
        raise HTTPException(
            409,
            (
                "The patient details differ from the verified record. "
                "Verify the actual patient or register new details."
            ),
        )
    if (
        caregiver_identity
        and body.caregiver
        and body.caregiver.name.strip().casefold()
        != caregiver_identity["patient"]["name"].strip().casefold()
    ):
        raise HTTPException(409, "The caregiver name differs from the verified caregiver record.")
    if (
        patient_identity
        and caregiver_identity
        and patient_identity["patient_id"] == caregiver_identity["patient_id"]
    ):
        raise HTTPException(409, "The caregiver and dependent cannot use the same patient record.")
    proof = claim_proof(body.claim_key)
    session_id = str(uuid.uuid5(uuid.UUID(principal.device_id or ""), f"adaptive:{proof}"))
    key = f"adaptive:claim:{session_id}"
    fingerprint = hmac.new(
        body.claim_key.encode(),
        body.model_dump_json(exclude={"qr_token", "claim_key"}).encode(),
        hashlib.sha256,
    ).hexdigest()
    redis = redis_of(request)
    lock = redis.lock(f"{key}:lock", timeout=120, blocking=False)
    if not await lock.acquire():
        raise HTTPException(409, "Your intake is being prepared. Try again shortly.")
    try:
        saved = await redis.get(key)
        if saved and not hmac.compare_digest(str(saved), fingerprint):
            raise HTTPException(409, "This intake was already submitted with different details.")
        current = await intake.state(session_id)
        if current and not hmac.compare_digest(
            current.get("registration_fingerprint", ""), fingerprint
        ):
            raise HTTPException(409, "This visit was already registered with different details.")
        async with clinic_session(principal.clinic_id) as db:
            row = await db.get(PatientSession, uuid.UUID(session_id))
        if row is not None and not current and not saved:
            raise HTTPException(
                409,
                (
                    "Reception must recover this incomplete registration. "
                    "Start a new check-in with their help."
                ),
            )
        if row is not None and row.status in CLOSED:
            raise HTTPException(409, "This visit has ended. Start a new check-in.")
        await redis.set(key, fingerprint, ex=3600)
        now = utc_now()
        language = Language(body.language.value if body.language else "en")
        identifier = (
            PatientIdentifier(**patient_identity["identifier"])
            if patient_identity
            else PatientIdentifier(system="urn:careflow:adaptive", value=session_id)
        )
        if row is None:
            if patient_identity:
                fhir_id = patient_identity["patient_id"]
            else:
                resource: dict[str, Any] = {
                    "resourceType": "Patient",
                    "active": True,
                    "identifier": [{"system": identifier.system, "value": identifier.value}],
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
                            resource,
                            if_none_exist=f"identifier={identifier.system}|{identifier.value}",
                        )
                except FhirError as exc:
                    raise HTTPException(
                        503,
                        (
                            "The patient record service is unavailable. "
                            "Your details remain on this screen; try again."
                        ),
                    ) from exc
                fhir_id = created.resource_id
            row = PatientSession(
                id=uuid.UUID(session_id),
                clinic_id=principal.clinic_id,
                device_id=uuid.UUID(principal.device_id or ""),
                fhir_patient_id=fhir_id,
                language=language.value,
                status="intake",
                started_at=now,
                expires_at=now + SESSION_EXPIRY,
                patient_display=" ".join(f"{p[0].upper()}." for p in body.patient.name.split()[:2])[
                    :8
                ],
            )
            async with clinic_session(principal.clinic_id) as db:
                db.add(row)
        if await get_consent(principal.clinic_id, session_id) is None:
            await record_consent(
                principal.clinic_id,
                session_id,
                body.consent,
                language=row.language,
                events=request.app.state.events,
                now=now,
            )
        if current is None:
            fetched = await intake.ports.ehr.fetch_context(identifier, str(principal.clinic_id))
            if (
                fetched.patient_ref is not None
                and fetched.patient_ref != f"Patient/{row.fhir_patient_id}"
            ):
                raise HTTPException(
                    409,
                    (
                        "The record changed during verification. "
                        "Ask reception to confirm the actual patient."
                    ),
                )
            if fetched.patient_ref is None:
                fetched = EhrFetch(
                    status=EhrStatus.unavailable,
                    fetched_at=now,
                    errors=["The confirmed patient's history could not be retrieved."],
                )
            context = ReceptionContext.model_validate(
                {
                    "patient": body.patient.model_dump(mode="json", exclude_none=True),
                    "department_id": department.id,
                    "department_name": "To be confirmed by care team"
                    if department.id == "unsure"
                    else department.name,
                    **({"doctor_id": doctor.id, "doctor_name": doctor.name} if doctor else {}),
                    "visit_purpose": "consultation",
                    "reason": body.reason or "",
                    "emergency_symptoms": [s.value for s in body.emergency_symptoms],
                    "registered_at": row.started_at,
                    "availability_notice": NOTICE,
                    "visit_for": visit_for,
                    "identity_status": "verified_demo" if patient_identity else "new_patient",
                    "intake_mode": "adaptive",
                    **(
                        {
                            "caregiver": body.caregiver.model_dump(
                                mode="json", exclude_none=True, exclude={"identity_token"}
                            )
                        }
                        if body.caregiver
                        else {}
                    ),
                }
            )
            turns, facts = registration_facts(body, now, session_id)
            state = cast(
                IntakeState,
                {
                    "session_id": session_id,
                    "clinic_id": str(principal.clinic_id),
                    "language": language,
                    "arrived_at": row.started_at,
                    "patient_type": "child" if body.patient.age_years < 16 else "adult",
                    "channel": channel,
                    "patient_identifier": identifier,
                    "patient_ref": f"Patient/{row.fhir_patient_id}",
                    "prefetched_ehr": fetched,
                    "facts": facts,
                    "turns": turns,
                    "reception_context": context.model_dump(mode="json", exclude_none=True),
                    "registration_fingerprint": fingerprint,
                },
            )
            try:
                result = await intake.start_adaptive(state)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            # Structured urgent answers notify the nurse now, before any measurement or AI turn.
            await intake.add_facts_and_evaluate(session_id, [])
            question = question_of(result) or pending_question(await intake.state(session_id))
        else:
            # A successful retry returns the latest question and never rewrites identity/consent.
            question = pending_question(current)
        if channel == "phone":
            await mark_handed_over(redis, session_id, proof)
        return CheckinResult(
            session_id=session_id,
            session_token=patient_token(
                settings, clinic_id=principal.clinic_id, session_id=session_id, channel=channel
            ),
            patient_display=row.patient_display or "Unknown",
            language=Language(row.language),
            first_question=question,
        )
    finally:
        await lock.release()
