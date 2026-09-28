"""Anonymous urgent assistance is consented, durable, sourced and independent of models/EHR."""

from __future__ import annotations

import datetime as dt
import secrets
import uuid
from typing import Any, cast

import pytest
from sqlalchemy import text
from starlette.testclient import TestClient

from careflow_api import ratelimit
from careflow_api.security import decode_token


@pytest.fixture(scope="module")
def clinics(clinics_factory: Any) -> Any:
    return clinics_factory()


@pytest.fixture(autouse=True)
def limits() -> None:
    ratelimit.key_prefix = f"cf:urgent:{uuid.uuid4().hex}"
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


def body() -> dict[str, Any]:
    return {
        "claim_key": secrets.token_urlsafe(32),
        "language": "en",
        "patient_type": "adult",
        "emergency_symptoms": [],
        "consent": {
            "camera": False,
            "voice": False,
            "data_use": True,
            "consent_version": "2026-09-29",
        },
    }


def nurse(flow: TestClient, clinics: Any) -> dict[str, str]:
    response = flow.post(
        "/v1/auth/login",
        json={"username": clinics.staff[("a", "nurse")], "password": "Test-Password-123"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_urgent_consent_and_entry_required(flow: TestClient, clinics: Any) -> None:
    data = body()
    assert flow.post("/v1/checkin/urgent", json=data).status_code == 401
    auth = kiosk(flow, clinics)
    data["consent"]["data_use"] = False
    assert flow.post("/v1/checkin/urgent", json=data, headers=auth).status_code == 422
    data["consent"]["data_use"] = True
    data["patient"] = {"name": "Invented"}
    assert flow.post("/v1/checkin/urgent", json=data, headers=auth).status_code == 422


def test_general_help_is_anonymous_durable_and_no_model_or_fhir(
    flow: TestClient, clinics: Any, database: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from careflow_integration.fhir.client import FhirClient

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Urgent assistance must not wait for a model or FHIR")

    runtime = cast(Any, flow.app).state.intake
    monkeypatch.setattr(type(runtime.ports.conversation), "next_question", forbidden)
    monkeypatch.setattr(type(runtime.ports.extractor), "extract", forbidden)
    monkeypatch.setattr(type(runtime.ports.urgency), "predict", forbidden)
    monkeypatch.setattr(FhirClient, "create", forbidden)
    monkeypatch.setattr(FhirClient, "search", forbidden)
    data, auth = body(), kiosk(flow, clinics)
    response = flow.post("/v1/checkin/urgent", json=data, headers=auth)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["patient_display"] == "Unknown"
    assert "first_question" not in result
    sid = result["session_id"]
    assert flow.portal is not None
    view = flow.portal.call(runtime.view, sid)
    assert view.waiting == "nurse_review"
    assert view.state["flags"]["assistance_requested"] is True
    assert view.state["patient_ref"] is None
    assert view.state["suggestion"] is None
    assert view.state.get("ehr_context") is None
    assert [e.key.value for e in view.state["facts"]] == ["chief_complaint"]
    assert all("extract" != node for node in view.state["node_trace"])
    with database.begin() as db:
        db.execute(text("SELECT set_config('app.clinic_id', :c, true)"), {"c": str(clinics.a)})
        patient_id = db.execute(
            text("SELECT fhir_patient_id FROM patient_session WHERE id=:s"), {"s": sid}
        ).scalar_one()
    assert patient_id is None
    worklist = flow.get("/v1/nurse/worklist", headers=nurse(flow, clinics))
    assert worklist.status_code == 200
    item = next(item for item in worklist.json() if item["session_id"] == sid)
    assert item["urgent_help"]["emergency_symptoms"] == []
    assert item["complaint"] == "Urgent assistance requested"
    assert "age_years" not in item and "sex" not in item
    assert "reception_context" not in item
    assert "token_code" not in item
    retry = flow.post("/v1/checkin/urgent", json=data, headers=auth)
    assert retry.status_code == 200 and retry.json()["session_id"] == sid
    flow.portal.call(cast(Any, flow.app).state.redis.delete, f"urgent:claim:{sid}")
    data["patient_type"] = "child"
    assert flow.post("/v1/checkin/urgent", json=data, headers=auth).status_code == 409


def test_urgent_phone_child_alerts_have_sources_but_no_age(flow: TestClient, clinics: Any) -> None:
    qr = flow.post("/v1/checkin/start/qr", headers=kiosk(flow, clinics), json={})
    data = body()
    data.update(
        qr_token=qr.json()["qr_token"],
        patient_type="child",
        emergency_symptoms=["face_droop", "drowsy_or_confused"],
    )
    response = flow.post("/v1/checkin/urgent", json=data)
    assert response.status_code == 200, response.text
    result = response.json()
    claims = decode_token(
        result["session_token"], secret="test-only-jwt-secret-not-used-anywhere-else-0000"
    )
    assert claims["channel"] == "phone"
    assert flow.portal is not None
    view = flow.portal.call(cast(Any, flow.app).state.intake.view, result["session_id"])
    assert view.waiting == "nurse_review"
    assert view.state["patient_type"] == "child"
    assert view.state["alerts"]
    assert not any(e.key.value.startswith("patient.") for e in view.state["facts"])
    for entry in view.state["facts"]:
        source = entry.fact.source
        assert source.quote == view.state["turns"][0].text[source.start : source.end]
    assert all(e.fact.value != "no" for e in view.state["facts"])


def test_anonymous_nurse_decision_assigns_real_queue_and_skips_ehr_write(
    flow: TestClient, clinics: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[bool] = []

    async def forbidden_summary(*args: Any, **kwargs: Any) -> Any:
        calls.append(True)
        raise AssertionError("Urgent assistance must not call a summary model")

    monkeypatch.setattr(cast(Any, flow.app).state.briefings, "summarizer", forbidden_summary)
    response = flow.post("/v1/checkin/urgent", json=body(), headers=kiosk(flow, clinics))
    assert response.status_code == 200, response.text
    result, auth = response.json(), nurse(flow, clinics)
    sid = result["session_id"]
    user = flow.get("/v1/auth/me", headers=auth).json()
    decision = {
        "decision_id": f"dec_{uuid.uuid4().hex[:12]}",
        "session_id": sid,
        "suggestion_id": None,
        "final_level": 3,
        "scale": "KTAS",
        "nurse_user_id": user["user_id"],
        "decided_at": dt.datetime.now(dt.UTC).isoformat(),
        "action": "CONFIRM",
    }
    response = flow.post(f"/v1/nurse/sessions/{sid}/decision", json=decision, headers=auth)
    assert response.status_code == 200, response.text
    token = response.json()["token"]
    patient = {"Authorization": f"Bearer {result['session_token']}"}
    status = flow.get(f"/v1/sessions/{sid}/status", headers=patient)
    assert status.status_code == 200
    assert status.json()["state"] == "queued"
    assert status.json()["token_code"] == token["token_code"]
    assert flow.portal is not None
    flow.portal.call(cast(Any, flow.app).state.briefings.drain)
    view = flow.portal.call(cast(Any, flow.app).state.intake.view, sid)
    assert view.state["patient_ref"] is None
    assert view.state["writeback_status"] in {"skipped", "pending"}
    assert not calls


def test_help_notification_is_clinic_scoped_and_staff_only() -> None:
    from careflow_api.events.tickets import TicketClaims
    from careflow_api.events.ws import Connection

    def connection(role: str, channel: str, clinic: str = "a") -> Connection:
        return Connection(
            websocket=cast(Any, None),
            claims=TicketClaims(
                role=role,
                clinic_id=clinic,
                subject=role,
                channel=cast(Any, channel),
                session_id="visit",
            ),
        )

    event = {"type": "patient.help_requested", "clinic_id": "a", "session_id": "visit"}
    assert connection("nurse", "staff").wants(event)
    assert connection("admin", "staff").wants(event)
    assert not connection("nurse", "staff", "b").wants(event)
    assert not connection("patient_session", "session").wants(event)
    assert not connection("doctor", "staff").wants(event)
    assert not connection("board", "staff").wants(event)
