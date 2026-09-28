"""Real registration persistence, structured safety, scoped authentication and no LLM calls."""

from __future__ import annotations

import secrets
import time
import uuid
from typing import Any

import pytest
from starlette.testclient import TestClient

from careflow_api import ratelimit
from careflow_api.security import decode_token


@pytest.fixture(scope="module")
def clinics(clinics_factory: Any) -> Any:
    return clinics_factory()


@pytest.fixture(autouse=True)
def limits() -> None:
    ratelimit.key_prefix = f"cf:reception:{uuid.uuid4().hex}"
    ratelimit.reset_memory()


def kiosk(flow: TestClient, clinics: Any) -> dict[str, str]:
    response = flow.post(
        "/v1/auth/device-token",
        json={
            "device_id": str(clinics.devices[("a", "kiosk")]),
            "device_type": "kiosk",
            "device_secret": "test-device-secret-000000",
        },
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def registration() -> dict[str, Any]:
    return {
        "claim_key": secrets.token_urlsafe(32),
        "language": "en",
        "patient": {"name": "Alex Reed", "age_years": 34, "sex": "male", "phone": "0000000000"},
        "department_id": "ent",
        "doctor_id": "demo_ent_01",
        "visit_purpose": "consultation",
        "reason": "Ear discomfort for two days",
        "emergency_symptoms": [],
        "consent": {
            "camera": False,
            "voice": False,
            "data_use": True,
            "consent_version": "2026-09-28",
        },
    }


def test_directory_has_full_example_hospital_options(flow: TestClient) -> None:
    response = flow.get("/v1/reception/directory")
    assert response.status_code == 200
    data = response.json()
    assert data["demo"] is True
    assert data["hospital_name"] == "Northstar General Hospital"
    assert {d["id"] for d in data["departments"]} >= {
        "ent",
        "ophthalmology",
        "womens_health",
        "unsure",
        "emergency",
    }


def test_registration_persists_and_skips_all_model_paths(
    flow: TestClient, clinics: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Registration must not invoke an LLM or urgency predictor")

    runtime = flow.app.state.intake
    monkeypatch.setattr(type(runtime.ports.conversation), "next_question", forbidden)
    monkeypatch.setattr(type(runtime.ports.extractor), "extract", forbidden)
    monkeypatch.setattr(type(runtime.ports.urgency), "predict", forbidden)
    data = registration()
    auth = kiosk(flow, clinics)
    start = time.monotonic()
    response = flow.post("/v1/reception/checkin", json=data, headers=auth)
    elapsed = time.monotonic() - start
    assert response.status_code == 200, response.text
    assert elapsed < 5, elapsed
    result = response.json()
    assert "first_question" not in result
    assert flow.portal is not None
    view = flow.portal.call(runtime.view, result["session_id"])
    assert view.waiting == "nurse_review"
    assert view.state["suggestion"] is None
    assert "extract" not in view.state["node_trace"]
    assert "run_rules" in view.state["node_trace"]
    assert view.state["patient_type"] == "adult"
    headers = {"Authorization": f"Bearer {result['session_token']}"}
    context = flow.get(f"/v1/sessions/{result['session_id']}/reception", headers=headers)
    assert context.status_code == 200
    assert context.json()["patient"]["name"] == "Alex Reed"
    assert context.json()["doctor_name"] == "Dr Samir Khan"
    assert context.json()["department_name"] == "Ear, nose and throat"
    assert (
        flow.get(f"/v1/sessions/{result['session_id']}/consent", headers=headers).json()["data_use"]
        is True
    )
    retry = flow.post("/v1/reception/checkin", json=data, headers=auth)
    assert retry.status_code == 200
    assert retry.json()["session_id"] == result["session_id"]
    data["reason"] = "Different reason"
    assert flow.post("/v1/reception/checkin", json=data, headers=auth).status_code == 409


def test_mobile_registration_structured_emergency_and_child(flow: TestClient, clinics: Any) -> None:
    code = flow.post("/v1/checkin/start/qr", headers=kiosk(flow, clinics), json={"language": "en"})
    data = registration()
    data["qr_token"] = code.json()["qr_token"]
    data["patient"]["age_years"] = 8
    data["emergency_symptoms"] = ["chest_pain", "short_of_breath", "drowsy_or_confused"]
    response = flow.post("/v1/reception/checkin", json=data)
    assert response.status_code == 200, response.text
    result = response.json()
    claims = decode_token(
        result["session_token"], secret="test-only-jwt-secret-not-used-anywhere-else-0000"
    )
    assert claims["channel"] == "phone"
    assert flow.portal is not None
    view = flow.portal.call(flow.app.state.intake.view, result["session_id"])
    assert view.waiting == "nurse_review"
    assert view.state["patient_type"] == "child"
    assert view.state["alerts"]
    assert all(entry.fact.value != "no" for entry in view.state["facts"])


def test_registration_validates_auth_department_and_consent(flow: TestClient, clinics: Any) -> None:
    data = registration()
    assert flow.post("/v1/reception/checkin", json=data).status_code == 401
    auth = kiosk(flow, clinics)
    data["consent"]["data_use"] = False
    assert flow.post("/v1/reception/checkin", json=data, headers=auth).status_code == 422
    data["consent"]["data_use"] = True
    data["doctor_id"] = "demo_cardiology_01"
    assert flow.post("/v1/reception/checkin", json=data, headers=auth).status_code == 422


def test_long_registration_reason_keeps_exact_fact_specific_provenance() -> None:
    import datetime as dt

    from careflow_api.routers.reception import registration_facts
    from careflow_contracts.api_models import ReceptionCheckinRequest

    data = registration()
    data["reason"] = "34 male chest_pain " + "x" * (1000 - len("34 male chest_pain "))
    data["patient"]["age_years"] = 8
    data["emergency_symptoms"] = ["chest_pain", "short_of_breath", "drowsy_or_confused"]
    turns, facts = registration_facts(
        ReceptionCheckinRequest.model_validate(data), dt.datetime.now(dt.UTC), str(uuid.uuid4())
    )
    assert len(turns[0].text) > 1000
    for entry in facts:
        source = entry.fact.source
        assert source.quote == turns[0].text[source.start : source.end]
        assert source.end - source.start == len(source.quote) <= 1000
    quotes = {entry.key.value: entry.fact.source.quote for entry in facts}
    assert quotes["chief_complaint"] == data["reason"]
    assert quotes["patient.age_years"] == "8"
    assert quotes["patient.sex"] == "male"
    assert quotes["chest_pain.present"] == "chest_pain"
    assert quotes["child_danger.hard_to_wake"] == "drowsy_or_confused"


def test_adult_emergency_choice_remains_visible_without_false_low_urgency(
    flow: TestClient, clinics: Any
) -> None:
    data = registration()
    data["department_id"] = "emergency"
    data["doctor_id"] = "demo_emergency_01"
    data["emergency_symptoms"] = ["drowsy_or_confused"]
    response = flow.post("/v1/reception/checkin", json=data, headers=kiosk(flow, clinics))
    assert response.status_code == 200, response.text
    assert flow.portal is not None
    view = flow.portal.call(flow.app.state.intake.view, response.json()["session_id"])
    assert view.waiting == "nurse_review"
    assert view.state["suggestion"] is None
    assert view.state.get("nurse_decision") is None
    assert view.state["reception_context"]["department_id"] == "emergency"
    assert view.state["reception_context"]["emergency_symptoms"] == ["drowsy_or_confused"]


def test_reception_nurse_decision_queues_and_builds_sourced_briefing_without_llm(
    flow: TestClient, clinics: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import datetime as dt

    called = []

    async def forbidden_summary(*args: Any, **kwargs: Any) -> Any:
        called.append(True)
        raise AssertionError("Reception must not call a summary model")

    monkeypatch.setattr(flow.app.state.briefings, "summarizer", forbidden_summary)
    registered = flow.post(
        "/v1/reception/checkin", json=registration(), headers=kiosk(flow, clinics)
    )
    assert registered.status_code == 200, registered.text
    result = registered.json()
    session_id = result["session_id"]
    logged = flow.post(
        "/v1/auth/login",
        json={"username": clinics.staff[("a", "nurse")], "password": "Test-Password-123"},
    )
    assert logged.status_code == 200
    nurse_headers = {"Authorization": f"Bearer {logged.json()['access_token']}"}
    me = flow.get("/v1/auth/me", headers=nurse_headers).json()
    decision = {
        "decision_id": f"dec_{uuid.uuid4().hex[:12]}",
        "session_id": session_id,
        "suggestion_id": None,
        "final_level": 4,
        "scale": "KTAS",
        "nurse_user_id": me["user_id"],
        "decided_at": dt.datetime.now(dt.UTC).isoformat(),
        "action": "CONFIRM",
    }
    before = time.monotonic()
    response = flow.post(
        f"/v1/nurse/sessions/{session_id}/decision", json=decision, headers=nurse_headers
    )
    assert response.status_code == 200, response.text
    assert time.monotonic() - before < 5
    token = response.json()["token"]
    assert token["department"] == "general"
    patient_headers = {"Authorization": f"Bearer {result['session_token']}"}
    before = time.monotonic()
    status = flow.get(f"/v1/sessions/{session_id}/status", headers=patient_headers)
    assert status.status_code == 200
    assert time.monotonic() - before < 2
    assert status.json()["token_code"] == token["token_code"]
    assert status.json()["state"] == "queued"
    assert flow.portal is not None
    flow.portal.call(flow.app.state.briefings.drain)
    saved = flow.portal.call(flow.app.state.briefings.store.get, clinics.a, uuid.UUID(session_id))
    assert saved["summary_status"] == "disabled"
    assert saved["sections"]
    assert not called
