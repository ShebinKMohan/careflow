"""Welcome QR starts a scoped phone visit without exposing kiosk credentials."""

from __future__ import annotations

import datetime as dt
import secrets
import uuid
from collections.abc import Iterator
from typing import Any

import jwt
import pytest
from starlette.testclient import TestClient

from careflow_api import ratelimit
from careflow_api.routers.start_checkin import ENTRY_AUDIENCE
from careflow_api.security import decode_token

KEY = "test-only-handover-signing-key-000000000000000000"
JWT_SECRET = "test-only-jwt-secret-not-used-anywhere-else-0000"


@pytest.fixture(scope="module")
def clinics(clinics_factory: Any) -> Any:
    return clinics_factory()


@pytest.fixture(autouse=True)
def fresh_limits() -> Iterator[None]:
    ratelimit.reset_memory()
    ratelimit.key_prefix = f"cf:entry:{uuid.uuid4().hex}"
    yield


def issue(flow: TestClient, clinics: Any) -> str:
    auth = flow.post(
        "/v1/auth/device-token",
        json={
            "device_id": str(clinics.devices[("a", "kiosk")]),
            "device_type": "kiosk",
            "device_secret": "test-device-secret-000000",
        },
    )
    assert auth.status_code == 200, auth.text
    qr = flow.post(
        "/v1/checkin/start/qr",
        headers={"Authorization": f"Bearer {auth.json()['access_token']}"},
        json={"language": "en"},
    )
    assert qr.status_code == 200, qr.text
    return qr.json()["qr_token"]


def body(code: str) -> dict[str, Any]:
    return {
        "qr_token": code,
        "claim_key": secrets.token_urlsafe(32),
        "digits": "100001",
        "language": "en",
        "consent": {
            "camera": False,
            "voice": False,
            "data_use": True,
            "consent_version": "2026-09-28",
        },
        "patient": {"patient_type": "adult"},
    }


def test_entry_requires_device_and_carries_no_patient_or_device_credential(
    flow: TestClient, clinics: Any
) -> None:
    assert flow.post("/v1/checkin/start/qr", json={}).status_code == 401
    code = issue(flow, clinics)
    claims = jwt.decode(code, KEY, algorithms=["HS256"], audience=ENTRY_AUDIENCE)
    assert set(claims) == {"clinic_id", "device_id", "language", "jti", "aud", "iat", "exp"}
    assert claims["exp"] - claims["iat"] == 600
    assert flow.get("/v1/auth/me", headers={"Authorization": f"Bearer {code}"}).status_code == 401


def test_phone_start_persists_consent_and_recovers_same_claim_only(
    flow: TestClient, clinics: Any
) -> None:
    data = body(issue(flow, clinics))
    first = flow.post("/v1/checkin/start/claim", json=data)
    assert first.status_code == 200, first.text
    result = first.json()
    token = decode_token(result["session_token"], secret=JWT_SECRET)
    assert token["channel"] == "phone"
    assert token["session_id"] == result["session_id"]
    auth = {"Authorization": f"Bearer {result['session_token']}"}
    consent = flow.get(f"/v1/sessions/{result['session_id']}/consent", headers=auth)
    assert consent.status_code == 200
    assert consent.json()["data_use"] is True
    assert consent.json()["voice"] is False
    assert result["first_question"]["key"] == "chief_complaint"
    retry = flow.post("/v1/checkin/start/claim", json=data)
    assert retry.status_code == 200, retry.text
    assert retry.json()["session_id"] == result["session_id"]
    other = {**data, "claim_key": secrets.token_urlsafe(32)}
    independent = flow.post("/v1/checkin/start/claim", json=other)
    assert independent.status_code == 200, independent.text
    assert independent.json()["session_id"] != result["session_id"]
    changed = {**data, "digits": "100002"}
    assert flow.post("/v1/checkin/start/claim", json=changed).status_code == 409


def test_consent_age_and_unknown_digits_fail_before_spending_code(
    flow: TestClient, clinics: Any
) -> None:
    data = body(issue(flow, clinics))
    data["consent"]["data_use"] = False
    assert flow.post("/v1/checkin/start/claim", json=data).status_code == 422
    data["consent"]["data_use"] = True
    data["patient"] = {"patient_type": "child"}
    assert flow.post("/v1/checkin/start/claim", json=data).status_code == 422
    data["patient"] = {"patient_type": "adult"}
    data["digits"] = "999999"
    assert flow.post("/v1/checkin/start/claim", json=data).status_code == 404
    data["digits"] = "100001"
    assert flow.post("/v1/checkin/start/claim", json=data).status_code == 200


@pytest.mark.parametrize("invalid", ["audience", "expired", "signature"])
def test_invalid_entry_code_is_refused(flow: TestClient, clinics: Any, invalid: str) -> None:
    code = issue(flow, clinics)
    claims = jwt.decode(code, KEY, algorithms=["HS256"], audience=ENTRY_AUDIENCE)
    key = KEY
    if invalid == "audience":
        claims["aud"] = "careflow-companion"
    elif invalid == "expired":
        claims["exp"] = int((dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)).timestamp())
    else:
        key = "wrong-test-signing-key-0000000000000000000000000"
    data = body(jwt.encode(claims, key, algorithm="HS256"))
    assert flow.post("/v1/checkin/start/claim", json=data).status_code == 401


def test_recovery_after_session_started_does_not_duplicate_visit(
    flow: TestClient, clinics: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from careflow_api.routers import start_checkin

    data = body(issue(flow, clinics))
    original = start_checkin.start_keypad_session
    seen = []

    async def interrupted(*args: Any, **kwargs: Any) -> Any:
        result = await original(*args, **kwargs)
        seen.append(result.session_id)
        if len(seen) == 1:
            from fastapi import HTTPException

            raise HTTPException(503, "Simulated connection lost after session creation")
        return result

    monkeypatch.setattr(start_checkin, "start_keypad_session", interrupted)
    assert flow.post("/v1/checkin/start/claim", json=data).status_code == 503
    retried = flow.post("/v1/checkin/start/claim", json=data)
    assert retried.status_code == 200, retried.text
    assert seen == [retried.json()["session_id"], retried.json()["session_id"]]


def test_ticket_cannot_cross_clinic_or_use_revoked_kiosk(
    flow: TestClient, clinics: Any, database: Any
) -> None:
    from sqlalchemy import text

    code = issue(flow, clinics)
    claims = jwt.decode(code, KEY, algorithms=["HS256"], audience=ENTRY_AUDIENCE)
    claims["clinic_id"] = str(clinics.b)
    crossed = jwt.encode(claims, KEY, algorithm="HS256")
    assert flow.post("/v1/checkin/start/claim", json=body(crossed)).status_code == 401
    with database.begin() as db:
        db.execute(text("SELECT set_config('app.clinic_id', :c, true)"), {"c": str(clinics.a)})
        db.execute(
            text("UPDATE device SET is_active=false WHERE id=:id"),
            {"id": str(clinics.devices[("a", "kiosk")])},
        )
    try:
        assert flow.post("/v1/checkin/start/claim", json=body(code)).status_code == 401
    finally:
        with database.begin() as db:
            db.execute(text("SELECT set_config('app.clinic_id', :c, true)"), {"c": str(clinics.a)})
            db.execute(
                text("UPDATE device SET is_active=true WHERE id=:id"),
                {"id": str(clinics.devices[("a", "kiosk")])},
            )


def test_phone_entry_limits_patient_code_guessing(flow: TestClient, clinics: Any) -> None:
    data = body(issue(flow, clinics))
    data["digits"] = "999999"
    for _ in range(5):
        assert flow.post("/v1/checkin/start/claim", json=data).status_code == 404
    data["digits"] = "100001"
    assert flow.post("/v1/checkin/start/claim", json=data).status_code == 429


def test_phone_entry_records_child_age(flow: TestClient, clinics: Any) -> None:
    data = body(issue(flow, clinics))
    data["patient"] = {"patient_type": "child", "age_years": 1, "age_months": 6}
    response = flow.post("/v1/checkin/start/claim", json=data)
    assert response.status_code == 200, response.text
    session_id = response.json()["session_id"]
    assert flow.portal is not None
    state = flow.portal.call(flow.app.state.intake.state, session_id)
    assert state["patient_type"] == "child"
    age_months = [
        entry.fact.value for entry in state["facts"] if entry.key.value == "patient.age_months"
    ]
    assert 18 in age_months
