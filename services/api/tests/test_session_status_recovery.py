"""Patient reloads recover the graph's pending question, without a database or model service."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from careflow_api.deps import Principal, get_principal
from careflow_api.intake.runtime import IntakeRuntime, intake_revision
from careflow_api.routers import sessions
from careflow_contracts import TurnIntent
from careflow_intake.checkpoint import memory_checkpointer
from careflow_intake.graph import build_graph
from careflow_intake.ports import Classified
from careflow_intake.stubs import DEFAULT_QUESTIONS, START, stub_ports

SESSION_ID = str(uuid.UUID("11111111-1111-4111-8111-111111111111"))
CLINIC_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
ROUTE = f"/v1/sessions/{SESSION_ID}/status"
TURN_ROUTE = f"/v1/sessions/{SESSION_ID}/turns"


@pytest.fixture
async def recovery(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[SimpleNamespace]:
    ports = stub_ports()
    intake = IntakeRuntime(build_graph(ports, memory_checkpointer()), ports)
    await intake.start(session_id=SESSION_ID, clinic_id=str(CLINIC_ID), arrived_at=START)
    principal = Principal(
        subject=f"session:{SESSION_ID}",
        role="patient_session",
        clinic_id=CLINIC_ID,
        token_id="test-phone-token",
        session_id=SESSION_ID,
        channel="phone",
    )
    app = FastAPI()
    app.include_router(sessions.router)
    app.dependency_overrides[get_principal] = lambda: principal
    app.dependency_overrides[sessions.runtime] = lambda: intake
    queue = SimpleNamespace(session_token=AsyncMock(return_value=None))
    app.state.queue = queue
    lookup = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(sessions, "session_row", lookup)
    monkeypatch.setattr(sessions, "touch", AsyncMock())

    @asynccontextmanager
    async def no_database(clinic_id: uuid.UUID) -> AsyncIterator[None]:
        assert clinic_id == CLINIC_ID
        yield None

    monkeypatch.setattr(sessions, "clinic_session", no_database)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://api.test") as http:
        yield SimpleNamespace(http=http, intake=intake, queue=queue, lookup=lookup)


async def test_reload_returns_the_current_question_after_an_answer(
    recovery: SimpleNamespace,
) -> None:
    first = await recovery.http.get(ROUTE)
    assert first.status_code == 200
    assert first.json()["intake_revision"] == 1
    assert first.json()["current_question"]["question_id"] == DEFAULT_QUESTIONS[0].question_id

    await recovery.intake.submit_turn(SESSION_ID, "My ankle hurts")
    response = await recovery.http.get(ROUTE)
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "intake"
    assert body["current_question"]["question_id"] == DEFAULT_QUESTIONS[1].question_id
    assert body["current_question"]["text"] == DEFAULT_QUESTIONS[1].text
    assert body["intake_revision"] == intake_revision(await recovery.intake.state(SESSION_ID))
    assert body["intake_revision"] > first.json()["intake_revision"]
    assert set(body) == {"state", "current_question", "intake_revision"}


async def test_recovery_survives_the_qr_channel_checkpoint_update(
    recovery: SimpleNamespace,
) -> None:
    await recovery.intake.submit_turn(SESSION_ID, "My ankle hurts")
    # claim_qr changes the graph channel before the phone is allowed to continue.
    await recovery.intake.update(SESSION_ID, {"channel": "phone"})
    response = await recovery.http.get(ROUTE)
    assert response.status_code == 200
    assert response.json()["current_question"]["question_id"] == DEFAULT_QUESTIONS[1].question_id


async def test_nurse_review_payload_never_reaches_a_patient(recovery: SimpleNamespace) -> None:
    for answer in ("My ankle hurts", "no", "no"):
        await recovery.intake.submit_turn(SESSION_ID, answer)
    waiting, pause = await recovery.intake.waiting_for(SESSION_ID)
    assert waiting == "nurse_review"
    assert pause["suggestion"] is not None
    response = await recovery.http.get(ROUTE)
    assert response.status_code == 200
    assert response.json() == {
        "state": "awaiting_nurse",
        "intake_revision": intake_revision(await recovery.intake.state(SESSION_ID)),
    }


@pytest.mark.parametrize("status", ["waiting", "called", "arrived", "closed", "no_show"])
async def test_queue_states_omit_even_a_remaining_patient_question(
    recovery: SimpleNamespace, status: str
) -> None:
    recovery.queue.session_token.return_value = SimpleNamespace(
        token_code="A-001", status=SimpleNamespace(value=status), room="Room A"
    )
    response = await recovery.http.get(ROUTE)
    assert response.status_code == 200
    assert response.json() == {
        "state": "queued" if status == "waiting" else status,
        "intake_revision": 1,
        "token_code": "A-001",
        "room": "Room A",
    }


async def test_state_and_pause_are_read_under_the_session_lock(
    recovery: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    intake: IntakeRuntime = recovery.intake
    original_state, original_waiting = intake.state, intake.waiting_for
    reads: list[str] = []

    async def locked_state(session_id: str):
        assert intake._locks[session_id].locked()
        reads.append("state")
        return await original_state(session_id)

    async def locked_waiting(session_id: str):
        assert intake._locks[session_id].locked()
        reads.append("waiting")
        return await original_waiting(session_id)

    monkeypatch.setattr(intake, "state", locked_state)
    monkeypatch.setattr(intake, "waiting_for", locked_waiting)
    assert (await recovery.http.get(ROUTE)).status_code == 200
    assert reads == ["state", "waiting"]


async def test_patient_cannot_recover_another_sessions_question(recovery: SimpleNamespace) -> None:
    response = await recovery.http.get(f"/v1/sessions/{uuid.uuid4()}/status")
    assert response.status_code == 403
    recovery.lookup.assert_not_awaited()


def test_revision_is_zero_without_any_checkpoint_turns() -> None:
    assert intake_revision(None) == 0
    assert intake_revision({}) == 0


async def test_late_original_answer_prevents_a_retry_from_answering_the_next_question(
    recovery: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = (await recovery.http.get(ROUTE)).json()
    original_submit = recovery.intake.submit_turn
    delayed, release = asyncio.Event(), asyncio.Event()

    async def delayed_submit(*args, **kwargs):
        # The original HTTP request can reach the session lock after a recovery GET has returned.
        delayed.set()
        await release.wait()
        return await original_submit(*args, **kwargs)

    monkeypatch.setattr(recovery.intake, "submit_turn", delayed_submit)
    body = {
        "text": "My ankle hurts",
        "input_mode": "text",
        "language": "en",
        "expected_intake_revision": before["intake_revision"],
    }
    original = asyncio.create_task(recovery.http.post(TURN_ROUTE, json=body))
    try:
        await asyncio.wait_for(delayed.wait(), timeout=2)
        recovery_status = (await recovery.http.get(ROUTE)).json()
        assert recovery_status == before  # the recovery GET alone cannot prove rejection
    finally:
        release.set()
    accepted = await original
    assert accepted.status_code == 200
    state_before_retry = await recovery.intake.state(SESSION_ID)
    assert accepted.json()["intake_revision"] == intake_revision(state_before_retry)

    retry = await recovery.http.post(TURN_ROUTE, json=body)
    assert retry.status_code == 409
    assert await recovery.intake.state(SESSION_ID) == state_before_retry
    assert (await recovery.http.get(ROUTE)).json()["current_question"]["question_id"] == (
        DEFAULT_QUESTIONS[1].question_id
    )


async def test_two_concurrent_submissions_at_one_revision_only_accept_one_answer(
    recovery: SimpleNamespace,
) -> None:
    revision = (await recovery.http.get(ROUTE)).json()["intake_revision"]
    body = {
        "text": "My ankle hurts",
        "input_mode": "text",
        "language": "en",
        "expected_intake_revision": revision,
    }
    responses = await asyncio.gather(
        recovery.http.post(TURN_ROUTE, json=body), recovery.http.post(TURN_ROUTE, json=body)
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    state = await recovery.intake.state(SESSION_ID)
    assert state["turn_count"] == 1
    assert sum(turn.role.value == "patient" for turn in state["turns"]) == 1
    accepted = next(response.json() for response in responses if response.status_code == 200)
    assert accepted["intake_revision"] == intake_revision(state)


async def test_side_question_advances_revision_even_when_question_and_answer_count_do_not(
    recovery: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Run the actual intent-router side-question branch: turn_count is decremented again there.
    monkeypatch.setattr(
        recovery.intake.ports.intents,
        "classify",
        AsyncMock(
            return_value=Classified(
                intent=TurnIntent(intake_answer=False, clinic_question=True, medical_question=False)
            )
        ),
    )
    await recovery.intake.update(SESSION_ID, {"channel": "phone"})
    before = (await recovery.http.get(ROUTE)).json()
    body = {
        "text": "Where is the pharmacy?",
        "input_mode": "text",
        "language": "en",
        "expected_intake_revision": before["intake_revision"],
    }
    response = await recovery.http.post(TURN_ROUTE, json=body)
    assert response.status_code == 200
    after = (await recovery.http.get(ROUTE)).json()
    assert after["current_question"]["question_id"] == before["current_question"]["question_id"]
    assert after["intake_revision"] > before["intake_revision"]
    assert after["intake_revision"] == response.json()["intake_revision"]
    state = await recovery.intake.state(SESSION_ID)
    assert state["turn_count"] == 0
    assert len(state["side_turn_ids"]) == 1
    assert (await recovery.http.post(TURN_ROUTE, json=body)).status_code == 409
    assert await recovery.intake.state(SESSION_ID) == state


async def test_older_clients_can_still_answer_without_a_revision(recovery: SimpleNamespace) -> None:
    response = await recovery.http.post(
        TURN_ROUTE, json={"text": "My ankle hurts", "input_mode": "text", "language": "en"}
    )
    assert response.status_code == 200
    assert response.json()["intake_revision"] == intake_revision(
        await recovery.intake.state(SESSION_ID)
    )
