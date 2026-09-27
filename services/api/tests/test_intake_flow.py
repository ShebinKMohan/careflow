"""The M1-12 flow end to end on the real app, Postgres, Valkey and HAPI (the isolated test stack).

Keypad check-in of a seeded synthetic persona -> the intake turns -> device vitals -> nurse vitals
-> the nurse's decision -> the patient's status shows a token. Around it, the route guarantees:
422 for an override without a reason, 409 for a decision the graph is not waiting for, one
`nurse_decisions` row per decision, the ECG task completed in the facts and in HAPI, 429 on the
sixth failed keypad attempt with a generic 404 before it, two concurrent turns both applied in
order, "no suggestion" without a triage model (the nurse sets the level), clinic and role
isolation, and the admin status.

The app runs with its lifespan (intake runtime, event bus, outbox relay, write-back consumer)
and real ports. The free-text answers are extracted by replaying replies recorded from the local
Qwen3.5-9B model (`fixtures/flow/providers.cassette.json`); no model runs in CI. To record them
again, delete the cassette and run with the model up:

    scripts/with_llm.sh python3 scripts/stack.py run --stack careflow-test -- \\
        env CAREFLOW_FLOW_RECORD=1 uv run pytest services/api/tests/test_intake_flow.py

Needs the isolated test stack with the personas (`make test-stack`); skipped without HAPI.
"""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import Engine
from starlette.testclient import TestClient

from careflow_contracts.generated.fact_schema import IntegerFact, TriStateFact
from careflow_contracts.generated.structured_facts_schema import FactKey
from careflow_intake.facts import fact_class

pytestmark = pytest.mark.hapi

COMPLAINT = "I've had chest pain since this morning"
PASSWORD = "Test-Password-123"  # conftest.TEST_PASSWORD
DEVICE_SECRET = "test-device-secret-000000"  # conftest.TEST_DEVICE_SECRET
MAX_TURNS = 8  # config/intake.yaml max_turns
CHEST_PAIN_DIGITS, ROUTINE_DIGITS = "100001", "100005"
RECORDING = os.environ.get("CAREFLOW_FLOW_RECORD") == "1"  # conftest.FLOW_RECORDING


def fhir_base() -> str:  # conftest.fhir_base: the stack's HAPI
    return os.environ.get("FHIR_BASE_URL", "http://localhost:8080/fhir").rstrip("/")


@pytest.fixture(scope="module")
def clinics(clinics_factory: Any) -> Any:
    """This module's own clinics: its check-ins must not add sessions to the shared ones."""
    return clinics_factory()


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


def staff(
    client: TestClient, clinics: Any, role: str, letter: str = "a"
) -> tuple[dict[str, str], str]:
    body = {"username": clinics.staff[(letter, role)], "password": PASSWORD}
    response = client.post("/v1/auth/login", json=body)
    assert response.status_code == 200, response.text
    headers = bearer(response.json()["access_token"])
    me = client.get("/v1/auth/me", headers=headers).json()
    return headers, me["user_id"]


def check_in(
    client: TestClient, clinics: Any, kiosk: dict[str, str], digits: str
) -> dict[str, Any]:
    """A keypad check-in at clinic A's kiosk (a kiosk checks in only as itself, at its clinic)."""
    kiosk_id = str(clinics.devices[("a", "kiosk")])
    body = {"clinic_id": str(clinics.a), "kiosk_id": kiosk_id, "digits": digits, "language": "en"}
    response = client.post("/v1/checkin/keypad", json=body, headers=kiosk)
    assert response.status_code == 200, response.text
    return dict(response.json())


TEXT_ANSWERS = {
    "symptom_onset_at": "It started at about seven this morning",
    "chest_pain.onset": "At about seven this morning",
    "pain_location": "In the middle of my chest",
}


def answer_for(key: str) -> tuple[str, str]:
    """A patient's answer to a checklist question: a tap on the kiosk's buttons for a yes/no or
    0-10 question (chest pain yes, other danger signs no, pain 7), typed text otherwise."""
    kind = fact_class(FactKey(key))
    if issubclass(kind, TriStateFact):
        return "tap", "Yes" if key == "chest_pain.present" else "No"
    if issubclass(kind, IntegerFact):
        return "tap", "7"
    return "text", TEXT_ANSWERS.get(key, "Nothing else")


def turn(
    client: TestClient, session_id: str, patient: dict[str, str], mode: str, answer: str
) -> Any:  # the test client's response (Starlette's httpx2)
    body = {"input_mode": mode, "text": answer, "language": "en"}
    return client.post(f"/v1/sessions/{session_id}/turns", json=body, headers=patient)


def decision(session_id: str, nurse_id: str, level: int, **extra: Any) -> dict[str, Any]:
    return {
        "decision_id": f"dec_{uuid.uuid4().hex[:12]}",
        "session_id": session_id,
        "suggestion_id": None,  # no triage model: nothing was suggested
        "final_level": level,
        "scale": "KTAS",
        "nurse_user_id": nurse_id,
        "decided_at": datetime.now(UTC).isoformat(),
        **extra,
    }


def decision_rows(database: Engine, clinic_id: uuid.UUID, session_id: str) -> list[Any]:
    with database.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.clinic_id', :c, true)"), {"c": str(clinic_id)}
        )
        return list(
            connection.execute(
                text(
                    "SELECT decision, final_level, suggested_level, suggestion_id, "
                    "override_reason_code, nurse_id FROM nurse_decisions WHERE session_id = :s"
                ),
                {"s": session_id},
            )
        )


def written(database: Engine, clinic_id: uuid.UUID, session_id: str) -> dict[str, str]:
    """The FHIR resources the write-back stored for the session: {local id: resource id}."""
    with database.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.clinic_id', :c, true)"), {"c": str(clinic_id)}
        )
        rows = connection.execute(
            text("SELECT local_id, resource_id FROM fhir_writebacks WHERE session_id = :s"),
            {"s": session_id},
        )
        return {row.local_id: row.resource_id for row in rows}


def item_arrival(client: TestClient, nurse: dict[str, str], session_id: str) -> str:
    rows = client.get("/v1/nurse/worklist", headers=nurse).json()
    return str(next(row for row in rows if row["session_id"] == session_id)["arrived_at"])


def test_keypad_to_token_on_the_real_stack(
    flow: TestClient, clinics: Any, database: Engine
) -> None:
    client = flow
    kiosk = device(client, clinics)
    nurse, nurse_id = staff(client, clinics, "nurse")

    # Check-in: the persona is found in the stack's HAPI; the patient token is for this session.
    started = check_in(client, clinics, kiosk, CHEST_PAIN_DIGITS)
    session_id, patient = started["session_id"], bearer(started["session_token"])
    question = started["first_question"]
    assert question["key"] == "chief_complaint"

    # The intake: the complaint typed, then the checklist, until it is done.
    answers = [("text", COMPLAINT)]
    response = turn(client, session_id, patient, *answers[0])
    assert response.status_code == 200, response.text
    while not response.json()["done"]:
        assert len(answers) < MAX_TURNS, "the intake never finished"
        mode, answer = answer_for(response.json()["next_question"]["key"])
        answers.append((mode, answer))
        response = turn(client, session_id, patient, mode, answer)
        assert response.status_code == 200, response.text
    assert len(answers) >= 3  # at least the three turns of the issue's flow
    before = client.get(f"/v1/sessions/{session_id}/triage", headers=nurse).json()["alerts"]
    first_raised = {alert["rule_id"]: alert["raised_at"] for alert in before}
    assert "CHEST_PAIN_ECG_10MIN" in first_raised

    # Device vitals (simulated device, DEVICE_MEASURED) and the nurse's vitals (full NEWS2).
    reading = {
        "device_id": str(clinics.devices[("a", "kiosk")]),
        "reading_id": f"rdg_{uuid.uuid4().hex[:10]}",
        "measured_at": datetime.now(UTC).isoformat(),
        "heart_rate": 104,
        "spo2": 96,
        "temperature_c": 37.1,
        "systolic_bp": 128,
        "diastolic_bp": 82,
    }
    vitals = client.post(f"/v1/sessions/{session_id}/vitals", json=reading, headers=kiosk)
    assert vitals.status_code in (200, 202), vitals.text
    nurse_vitals = {
        "respiratory_rate": 18,
        "systolic_bp": 126,
        "acvpu": "A",
        "on_supplemental_o2": False,
    }
    triage = client.post(
        f"/v1/nurse/sessions/{session_id}/vitals", json=nurse_vitals, headers=nurse
    )
    assert triage.status_code == 200, triage.text
    view = triage.json()
    assert view["news2"] and not view["news2"].get("partial"), view["news2"]
    rules = {alert["rule_id"] for alert in view["alerts"]}
    assert "CHEST_PAIN_ECG_10MIN" in rules  # safety rule 1: the rules run on the facts
    # Dated by the real clock, after the patient arrived (the stubs' fixed clock once leaked in).
    arrived = datetime.fromisoformat(item_arrival(client, nurse, session_id))
    for alert in view["alerts"]:
        raised = datetime.fromisoformat(alert["raised_at"])
        assert arrived <= raised <= datetime.now(UTC), (alert["rule_id"], raised, arrived)
        # The vitals re-ran the rules: an alert already raised keeps the time it was raised.
        if alert["rule_id"] in first_raised:
            assert raised == datetime.fromisoformat(first_raised[alert["rule_id"]]), alert

    # No triage model: the worklist says so, and the nurse must set the level.
    [item] = [
        row
        for row in client.get("/v1/nurse/worklist", headers=nurse).json()
        if row["session_id"] == session_id
    ]
    assert item.get("suggested_level") is None
    assert view["suggestion"] is None
    assert "CHEST_PAIN_ECG_10MIN" in {alert["rule_id"] for alert in item["alerts"]}

    # The patient never sees a level or an alert, and cannot decide.
    status = client.get(f"/v1/sessions/{session_id}/status", headers=patient).json()
    assert not {"level", "alerts", "suggestion"} & set(status)
    forbidden = client.post(
        f"/v1/nurse/sessions/{session_id}/decision",
        json=decision(session_id, nurse_id, 2, action="CONFIRM"),
        headers=patient,
    )
    assert forbidden.status_code == 403

    # 422: an override without a reason; the graph keeps waiting.
    no_reason = client.post(
        f"/v1/nurse/sessions/{session_id}/decision",
        json=decision(session_id, nurse_id, 2, action="OVERRIDE"),
        headers=nurse,
    )
    assert no_reason.status_code == 422, no_reason.text

    # The ECG task, before the decision: the fact is recorded now, HAPI gets it with the write-back.
    ecg = [task for task in view["open_tasks"] if task["kind"] == "ecg_within_10_min"]
    assert ecg, view["open_tasks"]

    # Confirm with the nurse's level: a token, one nurse_decisions row.
    decided = client.post(
        f"/v1/nurse/sessions/{session_id}/decision",
        json=decision(session_id, nurse_id, 2, action="CONFIRM"),
        headers=nurse,
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["decision"]["suggestion_id"] is None  # required, null: nothing suggested
    token = decided.json()["token"]
    assert token and token["session_id"] == session_id and token["token_code"]
    rows = decision_rows(database, clinics.a, session_id)
    assert [(r.decision, r.final_level, r.suggested_level, r.suggestion_id) for r in rows] == [
        ("confirm", 2, None, None)
    ]
    assert rows[0].nurse_id == nurse_id

    # 409: a second decision, the graph no longer waiting at nurse review.
    again = client.post(
        f"/v1/nurse/sessions/{session_id}/decision",
        json=decision(
            session_id, nurse_id, 3, action="OVERRIDE", override_reason_code="clinical_judgement"
        ),
        headers=nurse,
    )
    assert again.status_code == 409, again.text
    assert len(decision_rows(database, clinics.a, session_id)) == 1

    # The patient's status shows the token (and still no level).
    status = client.get(f"/v1/sessions/{session_id}/status", headers=patient).json()
    assert status["token_code"] == token["token_code"]
    assert not {"level", "alerts", "suggestion"} & set(status)

    # ECG done: the NURSE_CONFIRMED fact, and the HAPI Task completed.
    task_id = ecg[0]["task_id"]
    completed = client.post(
        f"/v1/nurse/sessions/{session_id}/tasks/{task_id}/complete", headers=nurse
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    # A card read again still shows the ECG done (M2-07): the task stays in `tasks`, completed.
    view = client.get(f"/v1/sessions/{session_id}/triage", headers=nurse).json()
    assert task_id not in {task["task_id"] for task in view.get("open_tasks", [])}
    assert {task["task_id"]: task["status"] for task in view["tasks"]}[task_id] == "completed"
    facts = client.get(f"/v1/sessions/{session_id}/facts", headers=nurse).json()
    ecg_done = facts["tasks"]["ecg_done_at"]
    assert ecg_done["provenance"] == "NURSE_CONFIRMED"
    resources = written(database, clinics.a, session_id)
    fhir_task = next(rid for local, rid in resources.items() if local.startswith("task-"))
    hapi_task = httpx.get(f"{fhir_base()}/Task/{fhir_task}", timeout=10).json()
    assert hapi_task["status"] == "completed"
    encounters = httpx.get(
        f"{fhir_base()}/Encounter",
        params={"identifier": f"urn:careflow:session|{session_id}"},
        timeout=10,
    ).json()
    assert encounters.get("total", len(encounters.get("entry", []))) == 1  # written once

    if os.environ.get("CAREFLOW_EVIDENCE") == "1":  # the observations docs/evidence quotes
        print(  # noqa: T201
            "\nEVIDENCE",
            {
                "session_id": session_id,
                "turns": [(mode, key) for mode, key in answers],
                "alerts": sorted(rules),
                "news2": {k: view["news2"].get(k) for k in ("total", "partial", "band")},
                "suggestion": view["suggestion"],
                "decision_rows": [tuple(r) for r in decision_rows(database, clinics.a, session_id)],
                "token_code": token["token_code"],
                "status_keys": sorted(status),
                "ecg_done": ecg_done["provenance"],
                "hapi_task": (fhir_task, hapi_task["status"]),
                "hapi_encounters": encounters.get("total"),
                "fhir": fhir_base(),
            },
        )

    # Clinic isolation: another clinic's nurse sees nothing of it.
    other_nurse, _ = staff(client, clinics, "nurse", "b")
    others = client.get("/v1/nurse/worklist", headers=other_nurse).json()
    assert session_id not in {row["session_id"] for row in others}
    assert client.get(f"/v1/sessions/{session_id}/triage", headers=other_nurse).status_code == 404
    # Role isolation: a kiosk cannot read the facts.
    assert client.get(f"/v1/sessions/{session_id}/facts", headers=kiosk).status_code == 403


def test_keypad_guessing_is_limited_with_a_generic_answer(flow: TestClient, clinics: Any) -> None:
    """Clinic B's kiosk, so clinic A's stays usable for the other tests."""
    kiosk = device(flow, clinics, "b")
    kiosk_id = str(clinics.devices[("b", "kiosk")])
    body = {"clinic_id": str(clinics.b), "kiosk_id": kiosk_id, "digits": "999990"}
    details = set()
    for attempt in range(5):
        body["digits"] = f"99999{attempt}"
        response = flow.post("/v1/checkin/keypad", json=body, headers=kiosk)
        assert response.status_code == 404, response.text
        details.add(response.json()["detail"])
    assert len(details) == 1  # the same words whatever the digits: nothing is revealed
    sixth = flow.post("/v1/checkin/keypad", json=body, headers=kiosk)
    assert sixth.status_code == 429, sixth.text


def test_two_concurrent_turns_are_both_applied_in_order(flow: TestClient, clinics: Any) -> None:
    kiosk = device(flow, clinics)
    started = check_in(flow, clinics, kiosk, ROUTINE_DIGITS)
    session_id, patient = started["session_id"], bearer(started["session_token"])
    texts = ["I twisted my ankle playing football", "It hurts when I walk on it"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(turn, flow, session_id, patient, "text", t) for t in texts]
        responses = [future.result() for future in futures]
    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    revisions = sorted(r.json()["intake_revision"] for r in responses)
    assert revisions[0] < revisions[1]  # serialised: one after the other, never both at once
    app: FastAPI = flow.app  # type: ignore[assignment]
    state = flow.portal.call(app.state.intake.state, session_id)  # type: ignore[union-attr]
    patient_turns = [t.text for t in state["turns"] if t.role.value == "patient"]
    assert sorted(patient_turns) == sorted(texts) and len(patient_turns) == 2  # none lost
    # In the order the server took them: the one with the lower revision came first.
    first = min(responses, key=lambda r: r.json()["intake_revision"])
    assert patient_turns[0] == texts[responses.index(first)]


def test_admin_status_lists_ports_providers_and_versions(flow: TestClient, clinics: Any) -> None:
    admin, _ = staff(flow, clinics, "admin")
    body = flow.get("/v1/admin/status", headers=admin).json()
    names = {service["name"]: service for service in body["services"]}
    assert body["ruleset_version"] and body["api_version"]
    assert {names[name]["status"] for name in ("postgres", "valkey", "hapi-fhir")} == {"ok"}
    # The write-back consumer runs and this clinic's write-back succeeded.
    assert names["fhir-writeback"]["status"] == "ok", names["fhir-writeback"]
    assert (
        names["fhir-writeback"]["detail"]
        == "consumer consuming; 0 failed write-backs in this clinic"
    )
    assert {"speech:stt", "speech:tts"} <= set(names)  # down here: no CareFlow voice service
    assert names["urgency-model"]["status"] == "degraded"  # no triage model: no suggestion
    assert names["port:urgency"]["detail"].startswith("none (no suggestion)")
    extractor = names["port:extractor"]["detail"]
    assert extractor.startswith("LLM extractor") and ("replay" in extractor) != RECORDING
    providers = flow.get("/v1/admin/providers", headers=admin).json()["tasks"]
    extraction = next(row for row in providers if row["task"] == "extraction")
    assert extraction["provider"] == ("local" if RECORDING else "replay")
