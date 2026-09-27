"""\"I feel worse\" on the real stack (M2-07, SHE-302, ADR-038, ADR-049).

The real routes, Postgres, Valkey and the intake graph (the `flow` app from conftest): a phone
session from a real QR claim presses \"I feel worse\". Every press is stored, counted and told to
the nurse (`patient.feels_worse` through the outbox, never the note) before anything else; the
ticked answers become PATIENT_STATED facts and the rules run again. Synthetic personas only.
"""

from __future__ import annotations

import secrets
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from starlette.testclient import TestClient

from careflow_api import ratelimit

ROUTINE_DIGITS = "100005"
DEVICE_SECRET = "test-device-secret-000000"  # conftest.TEST_DEVICE_SECRET
PASSWORD = "Test-Password-123"  # conftest.TEST_PASSWORD


@pytest.fixture(scope="module")
def clinics(clinics_factory: Any) -> Any:
    return clinics_factory()


@pytest.fixture(autouse=True)
def fresh_limits() -> None:
    ratelimit.reset_memory()
    ratelimit.key_prefix = f"cf:feels:{uuid.uuid4().hex}"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def kiosk_device(client: TestClient, clinics: Any) -> dict[str, str]:
    body = {
        "device_id": str(clinics.devices[("a", "kiosk")]),
        "device_type": "kiosk",
        "device_secret": DEVICE_SECRET,
    }
    response = client.post("/v1/auth/device-token", json=body)
    assert response.status_code == 200, response.text
    return bearer(response.json()["access_token"])


def phone_session(
    client: TestClient, clinics: Any, *, child_age: int | None = None
) -> tuple[str, str, str]:
    """Check in at clinic A's kiosk, say who the patient is, hand over to a phone: (session id,
    kiosk token, phone token)."""
    device = kiosk_device(client, clinics)
    body = {
        "clinic_id": str(clinics.a),
        "kiosk_id": str(clinics.devices[("a", "kiosk")]),
        "digits": ROUTINE_DIGITS,
        "language": "en",
    }
    started = client.post("/v1/checkin/keypad", json=body, headers=device)
    assert started.status_code == 200, started.text
    session_id, kiosk_token = started.json()["session_id"], started.json()["session_token"]
    who = (
        {"patient_type": "child", "age_years": child_age}
        if child_age is not None
        else {"patient_type": "adult"}
    )
    said = client.post(f"/v1/sessions/{session_id}/patient", json=who, headers=bearer(kiosk_token))
    assert said.status_code in (200, 204), said.text
    code = client.post(f"/v1/sessions/{session_id}/qr", headers=bearer(kiosk_token))
    assert code.status_code == 200, code.text
    claimed = client.post(
        "/v1/checkin/qr/claim",
        json={"qr_token": code.json()["qr_token"], "claim_key": secrets.token_urlsafe(32)},
    )
    assert claimed.status_code == 200, claimed.text
    return session_id, kiosk_token, claimed.json()["session_token"]


def press(
    client: TestClient,
    token: str,
    *,
    chest: tuple[str, ...] = (),
    fast: tuple[str, ...] = (),
    drowsy: str = "no",
    note: str | None = None,
) -> Any:
    body: dict[str, Any] = {
        "answers": {
            "chest_or_breathing": list(chest),
            "face_arm_speech": list(fast),
            "drowsy_or_confused": drowsy,
        }
    }
    if note is not None:
        body["note"] = note
    return client.post("/v1/companion/feels-worse", json=body, headers=bearer(token))


def outbox(database: Engine, clinic_id: uuid.UUID, session_id: str) -> list[tuple[str, dict]]:
    with database.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.clinic_id', :c, true)"), {"c": str(clinic_id)}
        )
        rows = connection.execute(
            text(
                "SELECT event_type, payload FROM outbox_events WHERE session_id = :s ORDER BY seq"
            ),
            {"s": session_id},
        )
        return [(row.event_type, dict(row.payload)) for row in rows]


def rule_ids(client: TestClient, session_id: str, nurse: dict[str, str]) -> set[str]:
    view = client.get(f"/v1/sessions/{session_id}/triage", headers=nurse).json()
    return {alert["rule_id"] for alert in view["alerts"]}


def staff(client: TestClient, clinics: Any, role: str = "nurse") -> dict[str, str]:
    body = {"username": clinics.staff[("a", role)], "password": PASSWORD}
    response = client.post("/v1/auth/login", json=body)
    assert response.status_code == 200, response.text
    return bearer(response.json()["access_token"])


def pressed(database: Engine, clinic_id: uuid.UUID, session_id: str) -> list[Any]:
    with database.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.clinic_id', :c, true)"), {"c": str(clinic_id)}
        )
        return list(
            connection.execute(
                text(
                    "SELECT press_number, answer_codes, note FROM feels_worse_reports "
                    "WHERE session_id = :s ORDER BY press_number"
                ),
                {"s": session_id},
            )
        )


def test_all_no_still_tells_the_nurse_and_raises_nothing(
    flow: TestClient, clinics: Any, database: Engine
) -> None:
    session_id, _, phone = phone_session(flow, clinics)
    nurse = staff(flow, clinics)
    before = rule_ids(flow, session_id, nurse)
    answer = press(flow, phone)
    assert answer.status_code == 202, answer.text
    assert answer.json()["press_count"] == 1
    events = [
        payload
        for kind, payload in outbox(database, clinics.a, session_id)
        if kind == "patient.feels_worse"
    ]
    assert [event["data"]["danger_answer_codes"] for event in events] == [[]]
    assert [event["data"]["press_count"] for event in events] == [1]
    assert rule_ids(flow, session_id, nurse) == before  # no rule fired
    assert [row.answer_codes for row in pressed(database, clinics.a, session_id)] == [[]]


def test_fast_signs_raise_the_fast_alert_after_telling_the_nurse(
    flow: TestClient, clinics: Any, database: Engine
) -> None:
    session_id, _, phone = phone_session(flow, clinics)
    nurse = staff(flow, clinics)
    assert press(flow, phone, fast=("face_droop", "arm_weakness")).status_code == 202
    assert "FAST_STROKE" in rule_ids(flow, session_id, nurse)
    kinds = [kind for kind, _ in outbox(database, clinics.a, session_id)]
    assert "patient.feels_worse" in kinds and "alert.raised" in kinds
    assert kinds.index("patient.feels_worse") < kinds.index("alert.raised")  # the nurse first


def test_not_sure_is_a_red_answer_for_a_child(flow: TestClient, clinics: Any) -> None:
    session_id, _, phone = phone_session(flow, clinics, child_age=1)
    nurse = staff(flow, clinics)
    assert press(flow, phone, drowsy="not_sure").status_code == 202
    view = flow.get(f"/v1/sessions/{session_id}/triage", headers=nurse).json()
    assert "NG143_RED_FLAG" in {alert["rule_id"] for alert in view["alerts"]}


def test_drowsy_is_not_invented_as_a_fact_for_an_adult(flow: TestClient, clinics: Any) -> None:
    session_id, _, phone = phone_session(flow, clinics)
    nurse = staff(flow, clinics)
    assert press(flow, phone, drowsy="yes").status_code == 202
    facts = flow.get(f"/v1/sessions/{session_id}/facts", headers=nurse).json()
    assert "hard_to_wake" not in str(facts.get("child_danger") or {})
    item = next(
        row
        for row in flow.get("/v1/nurse/worklist", headers=nurse).json()
        if row["session_id"] == session_id
    )
    assert item["feels_worse"]["danger_answer_codes"] == ["drowsy_or_confused"]


def test_the_press_is_told_even_when_the_rules_fail(
    flow: TestClient, clinics: Any, database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id, _, phone = phone_session(flow, clinics)
    intake = flow.app.state.intake  # type: ignore[attr-defined]

    async def broken(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("rules engine down (injected)")

    monkeypatch.setattr(intake, "report_feels_worse", broken)
    answer = press(flow, phone, chest=("chest_pain",))
    assert answer.status_code == 202, answer.text
    events = [
        payload
        for kind, payload in outbox(database, clinics.a, session_id)
        if kind == "patient.feels_worse"
    ]
    assert [event["data"]["danger_answer_codes"] for event in events] == [["chest_pain"]]
    assert len(pressed(database, clinics.a, session_id)) == 1


def test_every_press_is_kept_and_counted(flow: TestClient, clinics: Any, database: Engine) -> None:
    session_id, _, phone = phone_session(flow, clinics)
    nurse = staff(flow, clinics)
    counts = [press(flow, phone).json()["press_count"] for _ in range(3)]
    assert counts == [1, 2, 3]
    assert [row.press_number for row in pressed(database, clinics.a, session_id)] == [1, 2, 3]
    item = next(
        row
        for row in flow.get("/v1/nurse/worklist", headers=nurse).json()
        if row["session_id"] == session_id
    )
    assert item["feels_worse"]["press_count"] == 3
    view = flow.get(f"/v1/sessions/{session_id}/triage", headers=nurse).json()
    assert view["feels_worse"]["press_count"] == 3  # also on the nurse's session card


def test_the_note_never_leaves_the_database(
    flow: TestClient, clinics: Any, database: Engine
) -> None:
    session_id, _, phone = phone_session(flow, clinics)
    note = "My left arm went numb a few minutes ago"
    assert press(flow, phone, fast=("arm_weakness",), note=note).status_code == 202
    rows = outbox(database, clinics.a, session_id)
    assert rows and all(note not in str(payload) for _, payload in rows)
    assert [row.note for row in pressed(database, clinics.a, session_id)] == [note]


def test_only_the_phone_of_an_open_visit_may_press(
    flow: TestClient, clinics: Any, database: Engine
) -> None:
    session_id, kiosk_token, phone = phone_session(flow, clinics)
    nurse = staff(flow, clinics)
    device = kiosk_device(flow, clinics)
    other_id, _, other_phone = phone_session(flow, clinics)
    for headers in (device, nurse, bearer(kiosk_token)):
        body = {
            "answers": {"chest_or_breathing": [], "face_arm_speech": [], "drowsy_or_confused": "no"}
        }
        refused = flow.post("/v1/companion/feels-worse", json=body, headers=headers)
        assert refused.status_code in (401, 403), (headers, refused.text)
        assert flow.get("/v1/companion/status", headers=headers).status_code in (401, 403)
    # Another session's phone presses for its own visit only: the session comes from the token.
    assert press(flow, other_phone).status_code == 202
    assert pressed(database, clinics.a, session_id) == []
    assert len(pressed(database, clinics.a, other_id)) == 1
    # A visit that has ended is refused: the nurse station no longer lists it.
    with database.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.clinic_id', :c, true)"), {"c": str(clinics.a)}
        )
        connection.execute(
            text("UPDATE patient_session SET status = 'closed' WHERE id = :s"), {"s": session_id}
        )
    ended = press(flow, phone)
    assert ended.status_code == 409
    assert "reception desk" in ended.json()["detail"]
    assert pressed(database, clinics.a, session_id) == []
