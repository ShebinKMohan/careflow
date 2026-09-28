"""Synthetic hospital identity lookup: scoped entry, masked match, then demo birth-year check.

This is deliberately disabled outside dev/test and the demo HAPI adapter. Knowledge of a mobile
number and birth year is not production identity verification, and the demo FHIR store is shared.
No lookup value, patient record, identity token or clinical history is logged by these routes.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import secrets
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select

from careflow_api.config import Settings, get_settings
from careflow_api.deps import Principal, bearer, get_principal
from careflow_api.handover import claim_proof
from careflow_api.hospital import fhir_endpoint, hospital_adapter
from careflow_api.intake.runtime import IntakeRuntime
from careflow_api.models import Device
from careflow_api.ratelimit import failure_limit, rate_limit
from careflow_api.rls import clinic_session
from careflow_api.routers.handover import redis_of, signing_key
from careflow_api.routers.sessions import initials, runtime, utc_now
from careflow_api.routers.start_checkin import verify_entry
from careflow_contracts.api_models import (
    IdentityHistory,
    IdentityLookupRequest,
    IdentityLookupResult,
    IdentityVerifyRequest,
    IdentityVerifyResult,
    ReceptionPatient,
)
from careflow_contracts.generated.ehr_fetch_schema import PatientIdentifier
from careflow_integration.adapters import mapper
from careflow_integration.fhir.client import FhirClient, FhirError

router = APIRouter(tags=["checkin"])
CHALLENGE_TTL = 5 * 60
VERIFICATION_TTL = 20 * 60


def require_demo(settings: Settings) -> None:
    if settings.env not in {"dev", "test"} or hospital_adapter().id != "demo_hapi":
        raise HTTPException(
            403,
            (
                "Demo identity checks are disabled here. "
                "Please use the hospital's verified identity process."
            ),
        )


async def entry_principal(
    qr_token: str | None,
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
    settings: Settings,
) -> tuple[Principal, str]:
    if qr_token:
        entry = verify_entry(signing_key(settings), qr_token)
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
            raise HTTPException(403, "An authorized kiosk or welcome QR code is required.")
        channel = "kiosk"
    async with clinic_session(principal.clinic_id) as db:
        device = (
            await db.execute(
                select(Device).where(Device.id == uuid.UUID(principal.device_id or ""))
            )
        ).scalar_one_or_none()
    if device is None or not device.is_active or device.kind != "kiosk":
        raise HTTPException(401, "This kiosk is no longer available.")
    return principal, channel


def token_key(kind: str, token: str) -> str:
    return f"identity:{kind}:{hashlib.sha256(token.encode()).hexdigest()}"


def bound(record: dict[str, Any], principal: Principal, claim_key: str) -> bool:
    return (
        record.get("clinic_id") == str(principal.clinic_id)
        and record.get("device_id") == principal.device_id
        and hmac.compare_digest(record.get("claim_proof", ""), claim_proof(claim_key))
    )


async def verified_identity(
    token: str, principal: Principal, claim_key: str, request: Request, settings: Settings
) -> dict[str, Any]:
    require_demo(settings)
    raw = await redis_of(request).get(token_key("verified", token))
    record = json.loads(raw) if raw else None
    if not record or not bound(record, principal, claim_key):
        raise HTTPException(
            401,
            "This identity confirmation expired or belongs to a different check-in. Verify again.",
        )
    return record


def phone_value(value: str) -> str:
    return re.sub(r"[^0-9+]", "", value)


async def find_patient(kind: str, value: str) -> dict[str, Any] | None:
    async with FhirClient(fhir_endpoint()) as client:
        if kind == "patient_id":
            found = await client.search("Patient", {"identifier": value.strip()})
            found = [
                p
                for p in found
                if any(str(i.get("value")) == value.strip() for i in p.get("identifier", []))
            ]
        else:
            normalized = phone_value(value)
            if not 7 <= len(normalized.lstrip("+")) <= 15:
                raise HTTPException(
                    422,
                    (
                        "Enter the patient's complete mobile number, "
                        "including the country code if used in their record."
                    ),
                )
            results = await asyncio.gather(
                *(
                    client.search("Patient", {"telecom": variant})
                    for variant in {value.strip(), normalized}
                )
            )
            found = [
                p
                for result in results
                for p in result
                if any(
                    t.get("system") == "phone"
                    and phone_value(str(t.get("value", ""))) == normalized
                    for t in p.get("telecom", [])
                )
            ]
        unique = {p["id"]: p for p in found if p.get("id") and p.get("active", True)}
        return next(iter(unique.values())) if len(unique) == 1 else None


def patient_identifier(patient: dict[str, Any]) -> dict[str, str] | None:
    for item in patient.get("identifier", []):
        if item.get("system") and item.get("value"):
            return {"system": str(item["system"]), "value": str(item["value"])}
    return None


@router.post(
    "/v1/identity/lookup",
    response_model=IdentityLookupResult,
    response_model_exclude_none=True,
    dependencies=[Depends(rate_limit("identity-lookup-ip", limit=15, window_seconds=60))],
)
async def lookup(
    body: IdentityLookupRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> IdentityLookupResult:
    require_demo(settings)
    principal, _ = await entry_principal(body.qr_token, request, credentials, settings)
    if not body.consent.data_use:
        raise HTTPException(
            422, "Please agree to the hospital record lookup, or choose new patient registration."
        )
    allowed, failed = failure_limit(
        "identity-lookup", principal.device_id or "", limit=8, window_seconds=300
    )
    if not allowed:
        raise HTTPException(429, "Too many unsuccessful lookups. Please ask reception for help.")
    try:
        patient = await asyncio.wait_for(find_patient(body.kind.value, body.value), timeout=8)
    except (FhirError, TimeoutError):
        return IdentityLookupResult.model_validate(
            {
                "status": "unavailable",
                "message": (
                    "The demo record service is unavailable. "
                    "You may register your details for the care team."
                ),
                "demo": True,
            }
        )
    if patient is None or not patient_identifier(patient) or not patient.get("birthDate"):
        failed()
        return IdentityLookupResult.model_validate(
            {
                "status": "no_match",
                "message": (
                    "No unique matching demo record can be verified. "
                    "You can register as a new patient or ask reception."
                ),
                "demo": True,
            }
        )
    token = secrets.token_urlsafe(32)
    record = {
        "clinic_id": str(principal.clinic_id),
        "device_id": principal.device_id,
        "claim_proof": claim_proof(body.claim_key),
        "patient_id": patient["id"],
        "consent": body.consent.model_dump(mode="json"),
        "lookup_at": utc_now().isoformat(),
    }
    await redis_of(request).set(token_key("challenge", token), json.dumps(record), ex=CHALLENGE_TTL)
    return IdentityLookupResult.model_validate(
        {
            "status": "match",
            "challenge_token": token,
            "masked_name": initials(patient)[:8],
            "message": (
                "A demo record may match. Confirm the patient's year of birth before viewing it."
            ),
            "demo": True,
        }
    )


@router.post(
    "/v1/identity/verify",
    response_model=IdentityVerifyResult,
    response_model_exclude_none=True,
    dependencies=[Depends(rate_limit("identity-verify-ip", limit=10, window_seconds=60))],
)
async def verify(
    body: IdentityVerifyRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    intake: Annotated[IntakeRuntime, Depends(runtime)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> IdentityVerifyResult:
    require_demo(settings)
    principal, _ = await entry_principal(body.qr_token, request, credentials, settings)
    key = token_key("challenge", body.challenge_token)
    raw = await redis_of(request).get(key)
    record = json.loads(raw) if raw else None
    if not record or not bound(record, principal, body.claim_key):
        raise HTTPException(
            401, "This record check expired or belongs to another check-in. Start again."
        )
    allowed, failed = failure_limit(
        "identity-birth-year", key, limit=5, window_seconds=CHALLENGE_TTL
    )
    if not allowed:
        raise HTTPException(429, "Too many unsuccessful checks. Please ask reception for help.")
    try:
        async with FhirClient(fhir_endpoint()) as client:
            patient = await client.read("Patient", record["patient_id"])
    except FhirError as exc:
        raise HTTPException(503, "The record service is unavailable. Please try again.") from exc
    birth = str(patient.get("birthDate", ""))
    if not birth[:4].isdigit() or int(birth[:4]) != body.birth_year:
        failed()
        raise HTTPException(
            401, "These details do not match. Check the year of birth or ask reception."
        )
    names = patient.get("name") or [{}]
    name = (
        names[0].get("text")
        or " ".join([*(names[0].get("given") or []), names[0].get("family", "")]).strip()
    )
    mapped = mapper.patient(patient, utc_now())
    if mapped is None or mapped.age_years is None or not name:
        raise HTTPException(409, "This record needs reception assistance before check-in.")
    phone = next(
        (t.get("value") for t in patient.get("telecom", []) if t.get("system") == "phone"), None
    )
    profile = ReceptionPatient.model_validate(
        {
            "name": str(name)[:100],
            "age_years": int(mapped.age_years.value),
            "sex": patient.get("gender") or "unknown",
            **({"phone": str(phone)[:30]} if phone else {}),
        }
    )
    identifier = patient_identifier(patient)
    if identifier is None:
        raise HTTPException(409, "This record needs reception assistance before check-in.")
    fetched = await intake.ports.ehr.fetch_context(
        PatientIdentifier(**identifier), str(principal.clinic_id)
    )
    valid_history = (
        fetched.patient_ref == f"Patient/{record['patient_id']}" and fetched.context is not None
    )
    history = IdentityHistory.model_validate(
        {
            "status": fetched.status.value
            if valid_history and fetched.status.value in {"ok", "partial"}
            else "unavailable",
            "allergies": [],
            "conditions": [],
            "medications": [],
        }
    )
    if valid_history:
        for kind in ("allergies", "conditions", "medications"):
            entries = getattr(fetched.context, kind, []) or []
            setattr(
                history,
                kind,
                [
                    str(getattr(entry.value, "display", None) or entry.value.code)
                    for entry in entries
                ],
            )
    token = secrets.token_urlsafe(32)
    record |= {
        "patient": profile.model_dump(mode="json", exclude_none=True),
        "identifier": identifier,
        "verified_at": utc_now().isoformat(),
    }
    await redis_of(request).set(
        token_key("verified", token), json.dumps(record), ex=VERIFICATION_TTL
    )
    return IdentityVerifyResult(
        verification_token=token, patient=profile, history=history, demo=True
    )
