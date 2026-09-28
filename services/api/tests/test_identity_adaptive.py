"""Consent-gated demo identity and patient-isolated adaptive intake on the real stack."""

from __future__ import annotations

import secrets
import uuid
from typing import Any, cast

import pytest
from starlette.testclient import TestClient

from careflow_api import ratelimit
from careflow_api.security import decode_token

JWT_SECRET = "test-only-jwt-secret-not-used-anywhere-else-0000"


@pytest.fixture(scope="module")
def clinics(clinics_factory: Any) -> Any:
    return clinics_factory()


@pytest.fixture(autouse=True)
def fresh_limits() -> None:
    ratelimit.reset_memory()
    ratelimit.key_prefix = f"cf:identity-adaptive:{uuid.uuid4().hex}"


def kiosk(flow: TestClient, clinics: Any, letter: str = "a") -> dict[str, str]:
    response = flow.post(
        "/v1/auth/device-token",
        json={
            "device_id": str(clinics.devices[(letter, "kiosk")]),
            "device_type": "kiosk",
            "device_secret": "test-device-secret-000000",
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def welcome_qr(flow: TestClient, auth: dict[str, str]) -> str:
    response = flow.post("/v1/checkin/start/qr", headers=auth, json={"language": "en"})
    assert response.status_code == 200, response.text
    return response.json()["qr_token"]


def second_kiosk(flow: TestClient, clinics: Any, database: Any) -> dict[str, str]:
    from sqlalchemy import text

    device_id = uuid.uuid4()
    with database.begin() as db:
        db.execute(text("SELECT set_config('app.clinic_id', :c, true)"), {"c": str(clinics.a)})
        db.execute(
            text(
                "INSERT INTO device (id, clinic_id, kind, label, secret_hash) "
                "SELECT :id, clinic_id, kind, 'Second identity test kiosk', secret_hash "
                "FROM device WHERE id=:source"
            ),
            {"id": device_id, "source": clinics.devices[("a", "kiosk")]},
        )
    response = flow.post(
        "/v1/auth/device-token",
        json={
            "device_id": str(device_id),
            "device_type": "kiosk",
            "device_secret": "test-device-secret-000000",
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def consent() -> dict[str, Any]:
    return {
        "camera": False,
        "voice": False,
        "data_use": True,
        "consent_version": "2026-09-29",
    }


def claim_key() -> str:
    return secrets.token_urlsafe(32)


def lookup_body(value: str = "100001", *, key: str | None = None) -> dict[str, Any]:
    return {
        "claim_key": key or claim_key(),
        "kind": "patient_id",
        "value": value,
        "consent": consent(),
    }


def verified(
    flow: TestClient,
    auth: dict[str, str],
    *,
    key: str,
    value: str = "100001",
    birth_year: int = 1968,
    qr_token: str | None = None,
) -> dict[str, Any]:
    request = lookup_body(value, key=key)
    if qr_token:
        request["qr_token"] = qr_token
    found = flow.post("/v1/identity/lookup", json=request, headers=auth)
    assert found.status_code == 200, found.text
    assert found.json()["status"] == "match", found.text
    body = {
        "claim_key": key,
        "challenge_token": found.json()["challenge_token"],
        "birth_year": birth_year,
    }
    if qr_token:
        body["qr_token"] = qr_token
    confirmed = flow.post("/v1/identity/verify", json=body, headers=auth)
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()


def adaptive_body(*, key: str | None = None) -> dict[str, Any]:
    return {
        "claim_key": key or claim_key(),
        "language": "en",
        "patient": {"name": "Synthetic New Patient", "age_years": 34, "sex": "female"},
        "visit_for": "self",
        "consent": consent(),
    }


def session_state(flow: TestClient, session_id: str) -> Any:
    assert flow.portal is not None
    return flow.portal.call(cast(Any, flow.app).state.intake.state, session_id)


def test_lookup_requires_device_and_consent_before_any_ehr_read(
    flow: TestClient, clinics: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from careflow_integration.fhir.client import FhirClient

    auth = kiosk(flow, clinics)

    async def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Identity rejection must happen before an EHR read")

    monkeypatch.setattr(FhirClient, "search", forbidden)
    request = lookup_body()
    assert flow.post("/v1/identity/lookup", json=request).status_code == 401
    request["consent"]["data_use"] = False
    assert flow.post("/v1/identity/lookup", json=request, headers=auth).status_code == 422
    del request["consent"]
    assert flow.post("/v1/identity/lookup", json=request, headers=auth).status_code == 422


def test_lookup_reveals_only_masked_match_and_verify_requires_correct_birth_year(
    flow: TestClient, clinics: Any
) -> None:
    auth = kiosk(flow, clinics)
    request = lookup_body()
    found = flow.post("/v1/identity/lookup", json=request, headers=auth)
    assert found.status_code == 200, found.text
    result = found.json()
    assert result["status"] == "match"
    assert result["demo"] is True
    assert set(result) == {"status", "challenge_token", "masked_name", "message", "demo"}
    assert len(result["masked_name"]) <= 8
    assert "Mohammed" not in found.text
    assert "Saeed" not in found.text
    assert "persona-chest-pain" not in found.text
    assert "." not in result["challenge_token"]  # opaque capability, not readable JWT claims
    body = {
        "claim_key": request["claim_key"],
        "challenge_token": result["challenge_token"],
        "birth_year": 2001,
    }
    incorrect = flow.post("/v1/identity/verify", json=body, headers=auth)
    assert incorrect.status_code == 401
    assert "history" not in incorrect.json()
    assert "patient" not in incorrect.json()
    body["birth_year"] = 1968
    correct = flow.post("/v1/identity/verify", json=body, headers=auth)
    assert correct.status_code == 200, correct.text
    assert correct.json()["patient"]["sex"] == "male"
    assert correct.json()["history"]["status"] in {"ok", "partial", "unavailable"}
    assert "." not in correct.json()["verification_token"]


def test_unknown_record_discloses_no_identity_and_can_be_corrected(
    flow: TestClient, clinics: Any
) -> None:
    auth = kiosk(flow, clinics)
    request = lookup_body("unknown-synthetic-patient")
    missing = flow.post("/v1/identity/lookup", json=request, headers=auth)
    assert missing.status_code == 200, missing.text
    assert missing.json()["status"] == "no_match"
    assert set(missing.json()) == {"status", "message", "demo"}
    request["value"] = "100001"
    corrected = flow.post("/v1/identity/lookup", json=request, headers=auth)
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["status"] == "match"


@pytest.mark.parametrize("changed", ["claim_key", "clinic", "device"])
def test_challenge_cannot_be_verified_by_another_claim_or_clinic(
    flow: TestClient, clinics: Any, database: Any, changed: str
) -> None:
    auth = kiosk(flow, clinics)
    request = lookup_body()
    found = flow.post("/v1/identity/lookup", json=request, headers=auth)
    assert found.status_code == 200, found.text
    body = {
        "claim_key": claim_key() if changed == "claim_key" else request["claim_key"],
        "challenge_token": found.json()["challenge_token"],
        "birth_year": 1968,
    }
    if changed == "clinic":
        auth = kiosk(flow, clinics, "b")
    elif changed == "device":
        auth = second_kiosk(flow, clinics, database)
    rejected = flow.post("/v1/identity/verify", json=body, headers=auth)
    assert rejected.status_code == 401, rejected.text
    assert "patient" not in rejected.json()
    assert "history" not in rejected.json()


@pytest.mark.parametrize("changed", ["claim_key", "clinic", "device"])
def test_verified_identity_cannot_start_another_claim_clinic_or_device(
    flow: TestClient, clinics: Any, database: Any, changed: str
) -> None:
    auth = kiosk(flow, clinics)
    key = claim_key()
    identity = verified(flow, auth, key=key)
    body = adaptive_body(key=claim_key() if changed == "claim_key" else key)
    body.update(patient=identity["patient"], patient_identity_token=identity["verification_token"])
    if changed == "clinic":
        auth = kiosk(flow, clinics, "b")
    elif changed == "device":
        auth = second_kiosk(flow, clinics, database)
    rejected = flow.post("/v1/checkin/adaptive", json=body, headers=auth)
    assert rejected.status_code == 401, rejected.text
    assert "session_id" not in rejected.json()


def test_birth_year_guessing_is_limited_even_with_the_correct_year_afterward(
    flow: TestClient, clinics: Any
) -> None:
    auth = kiosk(flow, clinics)
    request = lookup_body()
    found = flow.post("/v1/identity/lookup", json=request, headers=auth)
    assert found.status_code == 200, found.text
    body = {
        "claim_key": request["claim_key"],
        "challenge_token": found.json()["challenge_token"],
        "birth_year": 2001,
    }
    for _ in range(5):
        assert flow.post("/v1/identity/verify", json=body, headers=auth).status_code == 401
    body["birth_year"] = 1968
    assert flow.post("/v1/identity/verify", json=body, headers=auth).status_code == 429


def test_expired_identity_capabilities_cannot_be_used(flow: TestClient, clinics: Any) -> None:
    from careflow_api.routers.identity import token_key

    auth = kiosk(flow, clinics)
    key = claim_key()
    identity = verified(flow, auth, key=key)
    assert flow.portal is not None
    flow.portal.call(
        cast(Any, flow.app).state.redis.delete,
        token_key("verified", identity["verification_token"]),
    )
    body = adaptive_body(key=key)
    body.update(patient=identity["patient"], patient_identity_token=identity["verification_token"])
    rejected = flow.post("/v1/checkin/adaptive", json=body, headers=auth)
    assert rejected.status_code == 401, rejected.text
    assert "session_id" not in rejected.json()


def test_verification_never_returns_another_patients_history(
    flow: TestClient, clinics: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader = cast(Any, flow.app).state.intake.ports.ehr
    original = type(reader).fetch_context

    async def wrong_patient(self: Any, identifier: Any, clinic_id: str) -> Any:
        result = await original(self, identifier, clinic_id)
        return result.model_copy(update={"patient_ref": "Patient/different-synthetic-patient"})

    monkeypatch.setattr(type(reader), "fetch_context", wrong_patient)
    identity = verified(flow, kiosk(flow, clinics), key=claim_key())
    assert identity["history"] == {
        "status": "unavailable",
        "allergies": [],
        "conditions": [],
        "medications": [],
    }


def test_new_patient_starts_real_question_and_persists_exact_consent(
    flow: TestClient, clinics: Any
) -> None:
    body = adaptive_body()
    response = flow.post("/v1/checkin/adaptive", json=body, headers=kiosk(flow, clinics))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["first_question"]["key"] == "chief_complaint"
    assert flow.portal is not None
    view = flow.portal.call(cast(Any, flow.app).state.intake.view, result["session_id"])
    assert view.waiting == "await_answer"
    assert view.state["pending_question"].key == result["first_question"]["key"]
    assert "ask" in view.state["node_trace"]
    assert view.state["reception_context"]["intake_mode"] == "adaptive"
    assert view.state["reception_context"]["identity_status"] == "new_patient"
    assert view.state["reception_context"]["visit_for"] == "self"
    auth = {"Authorization": f"Bearer {result['session_token']}"}
    saved = flow.get(f"/v1/sessions/{result['session_id']}/consent", headers=auth)
    assert saved.status_code == 200
    for field, value in consent().items():
        assert saved.json()[field] == value
    claims = decode_token(result["session_token"], secret=JWT_SECRET)
    assert claims["channel"] == "kiosk"


def test_verified_phone_patient_uses_own_ehr_and_no_mid_flow_login(
    flow: TestClient, clinics: Any
) -> None:
    qr = welcome_qr(flow, kiosk(flow, clinics))
    key = claim_key()
    identity = verified(flow, {}, key=key, qr_token=qr)
    body = adaptive_body(key=key)
    body.update(
        qr_token=qr,
        patient=identity["patient"],
        patient_identity_token=identity["verification_token"],
    )
    response = flow.post("/v1/checkin/adaptive", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    state = session_state(flow, result["session_id"])
    assert state["patient_ref"] == "Patient/persona-chest-pain"
    assert state["reception_context"]["identity_status"] == "verified_demo"
    assert state["reception_context"]["patient"] == identity["patient"]
    assert result["first_question"]["key"] == "chief_complaint"
    claims = decode_token(result["session_token"], secret=JWT_SECRET)
    assert claims["channel"] == "phone"
    assert claims["session_id"] == result["session_id"]


def test_verified_caregiver_never_becomes_new_childs_ehr(
    flow: TestClient, clinics: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = kiosk(flow, clinics)
    key = claim_key()
    caregiver = verified(flow, auth, key=key)
    reader = cast(Any, flow.app).state.intake.ports.ehr
    original = type(reader).fetch_context
    seen: list[str] = []

    async def record(self: Any, identifier: Any, clinic_id: str) -> Any:
        seen.append(identifier.value)
        assert identifier.value != "100001", "Caregiver EHR must not supply the child's facts"
        return await original(self, identifier, clinic_id)

    monkeypatch.setattr(type(reader), "fetch_context", record)
    body = adaptive_body(key=key)
    body["visit_for"] = "child"
    body["patient"] = {"name": "Synthetic Child", "age_years": 8, "sex": "female"}
    body["caregiver"] = {
        "name": caregiver["patient"]["name"],
        "relationship": "parent",
        "identity_token": caregiver["verification_token"],
    }
    response = flow.post("/v1/checkin/adaptive", json=body, headers=auth)
    assert response.status_code == 200, response.text
    state = session_state(flow, response.json()["session_id"])
    assert state["patient_type"] == "child"
    assert state.get("patient_ref") != "Patient/persona-chest-pain"
    assert "persona-chest-pain" not in str(state.get("ehr_context"))
    assert all(value != "100001" for value in seen)
    context = state["reception_context"]
    assert context["patient"]["name"] == "Synthetic Child"
    assert context["caregiver"]["name"] == caregiver["patient"]["name"]
    assert "identity_token" not in context["caregiver"]
    ages = [entry.fact.value for entry in state["facts"] if entry.key.value == "patient.age_years"]
    assert ages and all(age == 8 for age in ages)


def test_checkin_retry_recovers_session_but_cannot_change_patient_or_restore_consent(
    flow: TestClient, clinics: Any
) -> None:
    body = adaptive_body()
    auth = kiosk(flow, clinics)
    first = flow.post("/v1/checkin/adaptive", json=body, headers=auth)
    assert first.status_code == 200, first.text
    result = first.json()
    patient_auth = {"Authorization": f"Bearer {result['session_token']}"}
    url = f"/v1/sessions/{result['session_id']}/consent"
    revoked = {**consent(), "data_use": False}
    assert flow.post(url, json=revoked, headers=patient_auth).status_code == 200
    retry = flow.post("/v1/checkin/adaptive", json=body, headers=auth)
    assert retry.status_code == 200, retry.text
    assert retry.json()["session_id"] == result["session_id"]
    assert flow.get(url, headers=patient_auth).json()["data_use"] is False
    body["patient"]["name"] = "Different Synthetic Patient"
    assert flow.post("/v1/checkin/adaptive", json=body, headers=auth).status_code == 409
    assert session_state(flow, result["session_id"])["reception_context"]["patient"]["name"] == (
        "Synthetic New Patient"
    )


def test_durable_retry_fingerprint_survives_temporary_claim_record_loss(
    flow: TestClient, clinics: Any
) -> None:
    auth = kiosk(flow, clinics)
    body = adaptive_body()
    first = flow.post("/v1/checkin/adaptive", json=body, headers=auth)
    assert first.status_code == 200, first.text
    session_id = first.json()["session_id"]
    assert flow.portal is not None
    flow.portal.call(cast(Any, flow.app).state.redis.delete, f"adaptive:claim:{session_id}")
    unchanged = flow.post("/v1/checkin/adaptive", json=body, headers=auth)
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["session_id"] == session_id
    flow.portal.call(cast(Any, flow.app).state.redis.delete, f"adaptive:claim:{session_id}")
    body["reason"] = "A changed visit that must not replace the original patient's intake"
    changed = flow.post("/v1/checkin/adaptive", json=body, headers=auth)
    assert changed.status_code == 409, changed.text
    assert session_state(flow, session_id)["reception_context"]["reason"] == ""


def test_verified_child_keeps_own_history_with_a_separately_verified_caregiver(
    flow: TestClient, clinics: Any
) -> None:
    auth = kiosk(flow, clinics)
    key = claim_key()
    caregiver = verified(flow, auth, key=key)
    child = verified(flow, auth, key=key, value="100003", birth_year=2024)
    body = adaptive_body(key=key)
    body.update(
        visit_for="child",
        patient=child["patient"],
        patient_identity_token=child["verification_token"],
        caregiver={
            "name": caregiver["patient"]["name"],
            "relationship": "parent",
            "identity_token": caregiver["verification_token"],
        },
    )
    response = flow.post("/v1/checkin/adaptive", json=body, headers=auth)
    assert response.status_code == 200, response.text
    state = session_state(flow, response.json()["session_id"])
    assert state["patient_type"] == "child"
    assert state["patient_ref"] == "Patient/persona-child-drowsy"
    assert state["patient_identifier"].value == "100003"
    assert state["reception_context"]["patient"] == child["patient"]
    assert state["reception_context"]["caregiver"]["name"] == caregiver["patient"]["name"]
    ages = [entry.fact.value for entry in state["facts"] if entry.key.value == "patient.age_years"]
    assert child["patient"]["age_years"] in ages
    assert caregiver["patient"]["age_years"] not in ages


def test_changed_verified_profile_and_missing_dependent_details_fail_before_ehr_writes(
    flow: TestClient, clinics: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from careflow_integration.fhir.client import FhirClient

    auth = kiosk(flow, clinics)
    key = claim_key()
    identity = verified(flow, auth, key=key)

    async def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Rejected registration must not create an EHR patient")

    monkeypatch.setattr(FhirClient, "create", forbidden)
    body = adaptive_body(key=key)
    body.update(patient=identity["patient"], patient_identity_token=identity["verification_token"])
    body["patient"]["age_years"] = 8
    assert flow.post("/v1/checkin/adaptive", json=body, headers=auth).status_code == 409
    body = adaptive_body()
    body["consent"]["data_use"] = False
    assert flow.post("/v1/checkin/adaptive", json=body, headers=auth).status_code == 422
    body["consent"]["data_use"] = True
    body["visit_for"] = "child"
    body["patient"]["age_years"] = 8
    assert flow.post("/v1/checkin/adaptive", json=body, headers=auth).status_code == 422
    body["caregiver"] = {"name": "Synthetic Carer", "relationship": "parent"}
    body["patient"]["age_years"] = 34
    assert flow.post("/v1/checkin/adaptive", json=body, headers=auth).status_code == 422


def test_adaptive_rejects_history_that_drifted_to_another_verified_patient(
    flow: TestClient, clinics: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from careflow_api.handover import claim_proof

    auth = kiosk(flow, clinics)
    key = claim_key()
    identity = verified(flow, auth, key=key)
    reader = cast(Any, flow.app).state.intake.ports.ehr
    original = type(reader).fetch_context

    async def wrong_patient(self: Any, identifier: Any, clinic_id: str) -> Any:
        result = await original(self, identifier, clinic_id)
        return result.model_copy(update={"patient_ref": "Patient/persona-child-drowsy"})

    monkeypatch.setattr(type(reader), "fetch_context", wrong_patient)
    body = adaptive_body(key=key)
    body.update(patient=identity["patient"], patient_identity_token=identity["verification_token"])
    rejected = flow.post("/v1/checkin/adaptive", json=body, headers=auth)
    assert rejected.status_code == 409, rejected.text
    session_id = str(uuid.uuid5(clinics.devices[("a", "kiosk")], f"adaptive:{claim_proof(key)}"))
    assert session_state(flow, session_id) is None


@pytest.mark.parametrize("kind", ["mobile", "patient_id"])
async def test_exact_lookup_rejects_ambiguous_records_without_fetching_history(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    from careflow_api.routers import identity

    calls: list[tuple[str, dict[str, str]]] = []
    records = [
        {
            "id": f"synthetic-exact-{number}",
            "active": True,
            "identifier": [{"system": "urn:test:synthetic", "value": "test-patient-123"}],
            "telecom": [{"system": "phone", "value": "+44 0000 000123"}],
        }
        for number in (1, 2)
    ]

    class FakeFhirClient:
        def __init__(self, endpoint: str) -> None:
            pass

        async def __aenter__(self) -> FakeFhirClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def search(self, resource: str, query: dict[str, str]) -> list[dict[str, Any]]:
            calls.append((resource, query))
            assert resource == "Patient", "Lookup must not retrieve clinical history"
            return records

    monkeypatch.setattr(identity, "FhirClient", FakeFhirClient)
    value = "+440000000123" if kind == "mobile" else "test-patient-123"
    assert await identity.find_patient(kind, value) is None
    records.pop()
    assert await identity.find_patient(kind, value) == records[0]
    records[0]["active"] = False
    assert await identity.find_patient(kind, value) is None
    assert calls


async def test_mobile_lookup_ignores_partial_matches_and_deduplicates_same_patient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from careflow_api.routers import identity

    exact = {
        "id": "synthetic-correct",
        "telecom": [{"system": "phone", "value": "+44 0000 000123"}],
    }
    partial = {
        "id": "synthetic-other",
        "telecom": [{"system": "phone", "value": "+44 0000 0001234"}],
    }

    class FakeFhirClient:
        def __init__(self, endpoint: str) -> None:
            pass

        async def __aenter__(self) -> FakeFhirClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def search(self, resource: str, query: dict[str, str]) -> list[dict[str, Any]]:
            return [exact, partial]

    monkeypatch.setattr(identity, "FhirClient", FakeFhirClient)
    assert await identity.find_patient("mobile", "+44 0000 000123") == exact
