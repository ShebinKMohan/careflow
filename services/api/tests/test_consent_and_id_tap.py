"""Consent and the simulated ID-card tap (M2-05) on the real app, Postgres, Valkey and HAPI (the
isolated test stack), as test_intake_flow runs them.

Consent: every POST appends a record the app role can never change or delete, the latest wins, a
session without one has consented to nothing, and `consent.recorded` carries booleans only. The
voice turn follows the latest record: refused before any, allowed with voice and data use, refused
again once either is withdrawn; typed answers never need voice. A patient token reaches only its
own session's consent, and no other role reaches it at all.

ID tap: one FHIR Patient with the card's synthetic identifier checks in exactly as the keypad
does; none is a generic 404 that counts against the kiosk (429 after five); two are a 409 to
reception, with no session opened. The kiosk taps only as itself, at its own clinic.

Every test scans what was logged (INFO and up everywhere, DEBUG for CareFlow's own loggers) for
an unmasked card number, a token, a secret or the patient's words. SQLAlchemy's SQL echo is left
at WARNING: it prints statement parameters by design and is a debugging switch, never on in a
deployment.

The free-text answer is extracted by replaying the reply recorded for test_intake_flow
(`fixtures/flow/providers.cassette.json`); no model runs. Needs `make test-stack`; skipped
without HAPI.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import uuid
from collections.abc import Iterator
from typing import Any, Final

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from starlette.testclient import TestClient

from careflow_api.routers.sessions import AMBIGUOUS_CARD, UNKNOWN_CARD, VOICE_NOT_CONSENTED

pytestmark = pytest.mark.hapi

PASSWORD = "Test-Password-123"  # conftest.TEST_PASSWORD
DEVICE_SECRET = "test-device-secret-000000"  # conftest.TEST_DEVICE_SECRET
CHEST_PAIN_DIGITS: Final = "100001"
COMPLAINT: Final = "I've had chest pain since this morning"  # its extraction is recorded
# infra/seed/personas.yaml: persona-routine's keypad id and synthetic card, on the stack's HAPI.
ROUTINE_DIGITS, ROUTINE_CARD = "100005", "784-1992-1215279-9"
CARD_SYSTEM: Final = "https://careflow.example/fhir/sid/mock-national-id"
CONSENT_VERSION: Final = "2026-09-27.1"
AGREED: Final = {"camera": True, "voice": True, "data_use": True}
CARD_NUMBER: Final = re.compile(r"784-\d{4}-\d{7}-\d")


def fhir_base() -> str:  # conftest.fhir_base: the stack's HAPI
    return os.environ.get("FHIR_BASE_URL", "http://localhost:8080/fhir").rstrip("/")


@pytest.fixture(scope="module")
def clinics(clinics_factory: Any) -> Any:
    """This module's own clinics and kiosks: its failed taps and sessions touch no one else's."""
    return clinics_factory()


# --- the log scan ---

_STANDARD: Final = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}
_FORMAT: Final = logging.Formatter()


@pytest.fixture(autouse=True)
def logs(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    # Each call also sets the capture handler's level, so the lowest level comes last.
    caplog.set_level(logging.WARNING, logger="sqlalchemy")
    caplog.set_level(logging.INFO)
    for name in ("careflow_api", "careflow_intake", "careflow_integration", "careflow_providers"):
        caplog.set_level(logging.DEBUG, logger=name)
    yield caplog


def logged(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Each captured record as one text: logger, message, `extra` fields and any traceback."""
    lines = []
    for record in caplog.records:
        extra = {key: value for key, value in vars(record).items() if key not in _STANDARD}
        trace = _FORMAT.formatException(record.exc_info) if record.exc_info else ""
        lines.append(f"{record.name}: {record.getMessage()} {extra!r} {trace}")
    return lines


def assert_clean_logs(caplog: pytest.LogCaptureFixture, *private: str) -> None:
    """No unmasked card number, JSON web token, secret or patient text in any record."""
    assert caplog.records, "nothing was captured: the scan would prove nothing"
    for line in logged(caplog):
        assert not CARD_NUMBER.search(line), f"an unmasked card number was logged: {line}"
        assert "eyJ" not in line, f"a token was logged: {line}"
        for value in (PASSWORD, DEVICE_SECRET, *private):
            assert value not in line, f"{value!r} was logged: {line}"


# --- helpers ---


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def device(client: TestClient, clinics: Any, letter: str = "a") -> dict[str, str]:
    body = {
        "device_id": str(clinics.devices[(letter, "kiosk")]),
        "device_type": "kiosk",
        "device_secret": DEVICE_SECRET,
    }
    response = client.post("/v1/auth/device-token", json=body)
    assert response.status_code == 200, response.text
    return bearer(response.json()["access_token"])


def staff(client: TestClient, clinics: Any, role: str, letter: str = "a") -> dict[str, str]:
    body = {"username": clinics.staff[(letter, role)], "password": PASSWORD}
    response = client.post("/v1/auth/login", json=body)
    assert response.status_code == 200, response.text
    return bearer(response.json()["access_token"])


def clinic_of(clinics: Any, letter: str) -> uuid.UUID:
    return clinics.a if letter == "a" else clinics.b


def keypad(client: TestClient, clinics: Any, kiosk: dict[str, str], digits: str) -> Any:
    body = {
        "clinic_id": str(clinics.a),
        "kiosk_id": str(clinics.devices[("a", "kiosk")]),
        "digits": digits,
        "language": "en",
    }
    return client.post("/v1/checkin/keypad", json=body, headers=kiosk)


def id_tap(
    client: TestClient,
    clinics: Any,
    headers: dict[str, str],
    card: str,
    *,
    letter: str = "a",
    **overrides: str,
) -> Any:
    body = {
        "clinic_id": str(clinic_of(clinics, letter)),
        "kiosk_id": str(clinics.devices[(letter, "kiosk")]),
        "mock_card_payload": {"card_type": "mock_national_id", "id_number": card},
        "language": "en",
        **overrides,
    }
    return client.post("/v1/checkin/id-tap", json=body, headers=headers)


def checked_in(client: TestClient, clinics: Any, kiosk: dict[str, str]) -> tuple[str, str]:
    response = keypad(client, clinics, kiosk, CHEST_PAIN_DIGITS)
    assert response.status_code == 200, response.text
    return response.json()["session_id"], response.json()["session_token"]


def consent(client: TestClient, session_id: str, token: str, **choices: bool) -> Any:
    body = {**AGREED, **choices, "consent_version": CONSENT_VERSION}
    return client.post(f"/v1/sessions/{session_id}/consent", json=body, headers=bearer(token))


def turn(client: TestClient, session_id: str, token: str, mode: str, answer: str) -> Any:
    body = {"input_mode": mode, "text": answer, "language": "en"}
    return client.post(f"/v1/sessions/{session_id}/turns", json=body, headers=bearer(token))


def as_clinic(database: Engine, clinic_id: uuid.UUID, query: str, **params: Any) -> list[Any]:
    """Rows of `query`, read as the migrator under the clinic's row-level security."""
    with database.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.clinic_id', :c, true)"), {"c": str(clinic_id)}
        )
        return list(connection.execute(text(query), params))


def fresh_card() -> str:
    """A synthetic card number in the persona format that no seeded persona holds (born 2001)."""
    return f"784-2001-{secrets.randbelow(10**7):07d}-{secrets.randbelow(10)}"


# --- consent ---


def test_consent_is_appended_the_latest_wins_and_the_event_holds_booleans_only(
    flow: TestClient, clinics: Any, database: Engine, logs: pytest.LogCaptureFixture
) -> None:
    kiosk = device(flow, clinics)
    session_id, token = checked_in(flow, clinics, kiosk)
    url = f"/v1/sessions/{session_id}/consent"
    none_yet = flow.get(url, headers=bearer(token))
    assert none_yet.status_code == 404, none_yet.text  # no record: nothing is consented

    first = consent(flow, session_id, token)
    assert first.status_code == 200, first.text
    assert {k: first.json()[k] for k in (*AGREED, "consent_version", "session_id")} == {
        **AGREED,
        "consent_version": CONSENT_VERSION,
        "session_id": session_id,
    }
    second = consent(flow, session_id, token, camera=False, voice=False)
    assert second.status_code == 200, second.text
    latest = flow.get(url, headers=bearer(token)).json()
    assert (latest["camera"], latest["voice"], latest["data_use"]) == (False, False, True)
    assert latest["recorded_at"] >= first.json()["recorded_at"]

    rows = as_clinic(
        database,
        clinics.a,
        "SELECT camera, voice, data_use, consent_version, language FROM consent_records "
        "WHERE session_id = :s ORDER BY recorded_at",
        s=session_id,
    )
    assert [tuple(row) for row in rows] == [
        (True, True, True, CONSENT_VERSION, "en"),
        (False, False, True, CONSENT_VERSION, "en"),
    ]  # both kept: a new choice is a new row
    events = as_clinic(
        database,
        clinics.a,
        "SELECT payload FROM outbox_events WHERE session_id = :s AND event_type = "
        "'consent.recorded' ORDER BY seq",
        s=session_id,
    )
    assert [set(row.payload) for row in events] == [{"type", "timestamp", "data"}] * 2
    assert [row.payload["data"] for row in events] == [
        {"clinic_id": str(clinics.a), "session_id": session_id, "camera": True, "voice": True},
        {"clinic_id": str(clinics.a), "session_id": session_id, "camera": False, "voice": False},
    ]

    # Booleans and the text's version only: a free-text field is refused.
    note = {**AGREED, "consent_version": CONSENT_VERSION, "note": "anything"}
    refused = flow.post(url, json=note, headers=bearer(token))
    assert refused.status_code == 422, refused.text
    assert_clean_logs(logs, token)


def test_the_app_role_can_never_change_or_delete_a_consent_record(
    flow: TestClient, clinics: Any, logs: pytest.LogCaptureFixture
) -> None:
    kiosk = device(flow, clinics)
    session_id, token = checked_in(flow, clinics, kiosk)
    assert consent(flow, session_id, token).status_code == 200
    app_role = create_engine(
        os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://", 1)
    )
    try:
        for statement in (
            "UPDATE consent_records SET voice = false WHERE session_id = :s",
            "DELETE FROM consent_records WHERE session_id = :s",
        ):
            with pytest.raises(DBAPIError, match="permission denied"), app_role.begin() as db:
                db.execute(
                    text("SELECT set_config('app.clinic_id', :c, true)"), {"c": str(clinics.a)}
                )
                db.execute(text(statement), {"s": session_id})
    finally:
        app_role.dispose()
    assert flow.get(f"/v1/sessions/{session_id}/consent", headers=bearer(token)).json()[
        "voice"
    ]  # unchanged
    assert_clean_logs(logs, token)


def test_the_voice_turn_follows_the_latest_consent(
    flow: TestClient, clinics: Any, logs: pytest.LogCaptureFixture
) -> None:
    kiosk = device(flow, clinics)
    session_id, token = checked_in(flow, clinics, kiosk)

    def spoken() -> Any:
        return turn(flow, session_id, token, "voice", COMPLAINT)

    before = spoken()
    assert before.status_code == 403, before.text  # no record yet
    assert before.json()["detail"] == VOICE_NOT_CONSENTED
    assert consent(flow, session_id, token).status_code == 200
    allowed = spoken()
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["next_question"] is not None

    assert consent(flow, session_id, token, voice=False).status_code == 200  # revoked
    assert spoken().status_code == 403
    assert consent(flow, session_id, token, data_use=False).status_code == 200
    assert spoken().status_code == 403  # voice without data use is still no microphone
    assert consent(flow, session_id, token).status_code == 200
    assert spoken().status_code == 200  # agreed again

    # Declining voice never blocks the intake: typed and tapped answers need no voice consent.
    assert consent(flow, session_id, token, camera=False, voice=False).status_code == 200
    typed = turn(flow, session_id, token, "text", "It started at about seven this morning")
    assert typed.status_code == 200, typed.text
    assert_clean_logs(logs, token, COMPLAINT, "seven this morning")


def test_consent_is_only_the_patients_own_to_read_or_record(
    flow: TestClient, clinics: Any, logs: pytest.LogCaptureFixture
) -> None:
    kiosk = device(flow, clinics)
    session_id, token = checked_in(flow, clinics, kiosk)
    other_session, other_token = checked_in(flow, clinics, kiosk)
    url = f"/v1/sessions/{session_id}/consent"
    body = {**AGREED, "consent_version": CONSENT_VERSION}

    # Another patient's token, from the same kiosk and clinic: never this session's consent.
    assert flow.get(url, headers=bearer(other_token)).status_code == 403
    assert flow.post(url, json=body, headers=bearer(other_token)).status_code == 403
    # No other role reads or records a patient's consent: the kiosk, the staff, another clinic.
    others = [
        kiosk,
        staff(flow, clinics, "nurse"),
        staff(flow, clinics, "admin"),
        staff(flow, clinics, "nurse", "b"),
    ]
    for headers in others:
        assert flow.get(url, headers=headers).status_code == 403
        assert flow.post(url, json=body, headers=headers).status_code == 403
    assert flow.get(url).status_code == 401
    assert flow.post(url, json=body).status_code == 401
    # Nothing was recorded by any of them, and the other session still has none either.
    assert flow.get(url, headers=bearer(token)).status_code == 404
    other = flow.get(f"/v1/sessions/{other_session}/consent", headers=bearer(other_token))
    assert other.status_code == 404
    assert_clean_logs(logs, token, other_token)


# --- the ID tap ---


def test_id_tap_checks_in_the_cards_patient_exactly_as_the_keypad(
    flow: TestClient, clinics: Any, database: Engine, logs: pytest.LogCaptureFixture
) -> None:
    kiosk = device(flow, clinics)
    tapped = id_tap(flow, clinics, kiosk, ROUTINE_CARD)
    assert tapped.status_code == 200, tapped.text
    typed = keypad(flow, clinics, kiosk, ROUTINE_DIGITS)
    assert typed.status_code == 200, typed.text
    by_card, by_digits = tapped.json(), typed.json()
    assert by_card["session_id"] != by_digits["session_id"]  # each check-in is a new session
    for field in ("patient_display", "language", "first_question"):
        assert by_card[field] == by_digits[field], field
    assert ROUTINE_CARD not in tapped.text  # never echoed back

    [row] = as_clinic(
        database,
        clinics.a,
        "SELECT fhir_patient_id, status, device_id FROM patient_session WHERE id = :s",
        s=by_card["session_id"],
    )
    assert (row.fhir_patient_id, row.status) == ("persona-routine", "intake")
    assert row.device_id == clinics.devices[("a", "kiosk")]
    # The session token works for the new session, and only for it.
    patient = bearer(by_card["session_token"])
    status = flow.get(f"/v1/sessions/{by_card['session_id']}/status", headers=patient)
    assert status.status_code == 200, status.text
    other = flow.get(f"/v1/sessions/{by_digits['session_id']}/status", headers=patient)
    assert other.status_code == 403
    assert_clean_logs(logs, by_card["session_token"], by_digits["session_token"])


def test_id_tap_with_an_unknown_card_is_a_generic_404_then_429_for_the_kiosk(
    flow: TestClient, clinics: Any, logs: pytest.LogCaptureFixture
) -> None:
    """Clinic B's kiosk, so clinic A's stays usable for the other tests."""
    kiosk = device(flow, clinics, "b")
    details = set()
    for _ in range(5):
        response = id_tap(flow, clinics, kiosk, fresh_card(), letter="b")
        assert response.status_code == 404, response.text
        details.add(response.json()["detail"])
    assert details == {UNKNOWN_CARD}  # the same words whatever the card: nothing is revealed
    sixth = id_tap(flow, clinics, kiosk, ROUTINE_CARD, letter="b")
    assert sixth.status_code == 429, sixth.text  # even for a card that would match
    # The keypad counts its own guesses: the patient can still use it.
    body = {
        "clinic_id": str(clinics.b),
        "kiosk_id": str(clinics.devices[("b", "kiosk")]),
        "digits": ROUTINE_DIGITS,
    }
    assert flow.post("/v1/checkin/keypad", json=body, headers=kiosk).status_code == 200
    assert_clean_logs(logs)


@pytest.fixture
def duplicated_card() -> Iterator[str]:
    """Two synthetic Patients on the stack's HAPI holding one new card number; deleted after."""
    card = fresh_card()
    created: list[str] = []
    try:
        for given in ("One", "Two"):
            patient = {
                "resourceType": "Patient",
                "identifier": [{"system": CARD_SYSTEM, "value": card}],
                "name": [{"family": "Duplicate", "given": [f"Test{given}"]}],
                "meta": {"tag": [{"system": "urn:careflow:test", "code": "synthetic"}]},
            }
            response = httpx.post(f"{fhir_base()}/Patient", json=patient, timeout=10)
            assert response.status_code == 201, response.text
            created.append(response.json()["id"])
        yield card
    finally:
        for patient_id in created:
            httpx.delete(f"{fhir_base()}/Patient/{patient_id}", timeout=10)


def test_id_tap_matching_two_patients_sends_the_patient_to_reception(
    flow: TestClient,
    clinics: Any,
    database: Engine,
    duplicated_card: str,
    logs: pytest.LogCaptureFixture,
) -> None:
    kiosk = device(flow, clinics)
    count = "SELECT count(*) AS n FROM patient_session WHERE clinic_id = :c"
    before = as_clinic(database, clinics.a, count, c=clinics.a)[0].n
    response = id_tap(flow, clinics, kiosk, duplicated_card)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == AMBIGUOUS_CARD
    assert duplicated_card not in response.text
    assert as_clinic(database, clinics.a, count, c=clinics.a)[0].n == before  # no session
    assert_clean_logs(logs)


def test_id_tap_is_only_for_a_kiosk_as_itself_at_its_own_clinic(
    flow: TestClient, clinics: Any, logs: pytest.LogCaptureFixture
) -> None:
    kiosk = device(flow, clinics)
    other_kiosk = str(clinics.devices[("b", "kiosk")])
    elsewhere = id_tap(flow, clinics, kiosk, ROUTINE_CARD, clinic_id=str(clinics.b))
    assert elsewhere.status_code == 403, elsewhere.text
    impersonated = id_tap(flow, clinics, kiosk, ROUTINE_CARD, kiosk_id=other_kiosk)
    assert impersonated.status_code == 403, impersonated.text
    _, patient_token = checked_in(flow, clinics, kiosk)
    for headers in (
        staff(flow, clinics, "nurse"),
        staff(flow, clinics, "admin"),
        bearer(patient_token),
    ):
        assert id_tap(flow, clinics, headers, ROUTINE_CARD).status_code == 403
    assert id_tap(flow, clinics, {}, ROUTINE_CARD).status_code == 401
    # A malformed card is refused without being echoed back.
    malformed = id_tap(flow, clinics, kiosk, "784-1992-121527-9")
    assert malformed.status_code == 422, malformed.text
    assert "121527" not in malformed.text
    assert_clean_logs(logs, patient_token)
