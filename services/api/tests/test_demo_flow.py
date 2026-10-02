"""Original adaptive check-in, safety rules, nurse authority and queue with demo AI adapters."""

from __future__ import annotations

import os
import secrets
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from redis import Redis
from starlette.testclient import TestClient

from careflow_api import db, hospital, ratelimit
from careflow_api.config import get_settings
from careflow_api.main import create_app
from careflow_contracts.generated.fact_schema import IntegerFact, TriStateFact
from careflow_contracts.generated.structured_facts_schema import FactKey
from careflow_intake.facts import fact_class


@pytest.fixture(scope="module")
def clinics(clinics_factory: Any) -> Any:
    """Keep this demo's visits out of other tests' shared clinic fixtures."""
    return clinics_factory()


@pytest.mark.hapi
def test_demo_adaptive_checkin_to_nurse_confirmed_queue(
    database: Any,
    clinics: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fhir = os.environ["FHIR_BASE_URL"].rstrip("/")
    assert httpx.get(f"{fhir}/metadata", timeout=10).status_code == 200
    stream = f"careflow:test:demo:{uuid.uuid4().hex}"
    monkeypatch.setenv("CAREFLOW_ENV", "dev")
    monkeypatch.setenv("CAREFLOW_DEMO_MODE", "1")
    monkeypatch.setenv("TRIAGE_MODEL_PATH", str(tmp_path / "missing-trained-model"))
    monkeypatch.setenv("CAREFLOW_EVENT_STREAM", stream)
    get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.get_sessionmaker.cache_clear()
    hospital._adapter.cache_clear()  # noqa: SLF001 - select the isolated stack
    ratelimit.reset_memory()
    ratelimit.key_prefix = f"cf:demo-flow:{uuid.uuid4().hex}"
    try:
        with TestClient(create_app()) as client:
            auth = client.post(
                "/v1/auth/device-token",
                json={
                    "device_id": str(clinics.devices[("a", "kiosk")]),
                    "device_type": "kiosk",
                    "device_secret": "test-device-secret-000000",
                },
            )
            assert auth.status_code == 200, auth.text
            kiosk = {"Authorization": f"Bearer {auth.json()['access_token']}"}
            started = client.post(
                "/v1/checkin/adaptive",
                headers=kiosk,
                json={
                    "claim_key": secrets.token_urlsafe(32),
                    "language": "en",
                    "visit_for": "self",
                    "patient": {"name": "Synthetic Demo Patient", "age_years": 34, "sex": "female"},
                    "consent": {
                        "camera": False,
                        "voice": False,
                        "data_use": True,
                        "consent_version": "2026-09-29",
                    },
                },
            )
            assert started.status_code == 200, started.text
            session = started.json()["session_id"]
            patient = {"Authorization": f"Bearer {started.json()['session_token']}"}
            answer, mode = "I have chest pain", "text"
            for _ in range(8):
                response = client.post(
                    f"/v1/sessions/{session}/turns",
                    headers=patient,
                    json={
                        "text": answer,
                        "input_mode": mode,
                        "language": "en",
                    },
                )
                assert response.status_code == 200, response.text
                if response.json()["done"]:
                    break
                kind = fact_class(FactKey(response.json()["next_question"]["key"]))
                if issubclass(kind, TriStateFact):
                    answer, mode = "No", "tap"
                elif issubclass(kind, IntegerFact):
                    answer, mode = "7", "tap"
                else:
                    answer, mode = "It started this morning", "text"
            else:
                pytest.fail("original demo intake failed to reach nurse review")
            logged_in = client.post(
                "/v1/auth/login",
                json={
                    "username": clinics.staff[("a", "nurse")],
                    "password": "Test-Password-123",
                },
            )
            assert logged_in.status_code == 200, logged_in.text
            nurse = {"Authorization": f"Bearer {logged_in.json()['access_token']}"}
            view = client.get(f"/v1/sessions/{session}/triage", headers=nurse)
            assert view.status_code == 200, view.text
            assert view.json()["suggestion"] is None
            assert "CHEST_PAIN_ECG_10MIN" in {a["rule_id"] for a in view.json()["alerts"]}
            nurse_id = client.get("/v1/auth/me", headers=nurse).json()["user_id"]
            decision = {
                "decision_id": f"dec_{uuid.uuid4().hex[:12]}",
                "session_id": session,
                "suggestion_id": None,
                "final_level": 2,
                "scale": "KTAS",
                "nurse_user_id": nurse_id,
                "decided_at": datetime.now(UTC).isoformat(),
                "action": "CONFIRM",
            }
            endpoint = f"/v1/nurse/sessions/{session}/decision"
            assert client.post(endpoint, headers=patient, json=decision).status_code == 403
            confirmed = client.post(endpoint, headers=nurse, json=decision)
            assert confirmed.status_code == 200, confirmed.text
            token = confirmed.json()["token"]["token_code"]
            status = client.get(f"/v1/sessions/{session}/status", headers=patient).json()
            assert status["token_code"] == token
            assert not {"level", "alerts", "suggestion"} & set(status)
    finally:
        get_settings.cache_clear()
        db.get_engine.cache_clear()
        db.get_sessionmaker.cache_clear()
        redis = Redis.from_url(os.environ["REDIS_URL"])
        redis.delete(stream, f"{stream}:dead")
        redis.close()
