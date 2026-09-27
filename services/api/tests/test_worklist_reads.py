"""The nurse's worklist reads each open session's checkpoint once, concurrently, and every queue
token in one query (SHE-325), and says exactly what the one-by-one reads said. Its reload sits on
the path of every deterioration alert, so a read per session per step made the alert's way to the
nurse grow with the open visits.

The real app on the isolated test stack (the `flow` fixture): skipped without HAPI.
"""

from __future__ import annotations

import uuid
from collections import Counter
from typing import Any, cast

import pytest
from starlette.testclient import TestClient

from careflow_api import ratelimit
from careflow_api.feels_worse import summaries as feels_worse_summaries
from careflow_api.intake.runtime import IntakeRuntime
from careflow_api.intake.views import worklist_item
from careflow_api.nurse_flags import medical_questions
from careflow_api.queue.service import QueueService
from careflow_api.rls import clinic_session

ROUTINE_DIGITS = "100005"
DEVICE_SECRET = "test-device-secret-000000"  # conftest.TEST_DEVICE_SECRET
PASSWORD = "Test-Password-123"  # conftest.TEST_PASSWORD


@pytest.fixture(scope="module")
def clinics(clinics_factory: Any) -> Any:
    return clinics_factory()


@pytest.fixture(autouse=True)
def fresh_limits() -> None:
    ratelimit.reset_memory()
    ratelimit.key_prefix = f"cf:worklist:{uuid.uuid4().hex}"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def staff(client: TestClient, clinics: Any) -> dict[str, str]:
    body = {"username": clinics.staff[("a", "nurse")], "password": PASSWORD}
    response = client.post("/v1/auth/login", json=body)
    assert response.status_code == 200, response.text
    return bearer(response.json()["access_token"])


def check_in(client: TestClient, clinics: Any) -> str:
    """A keypad check-in at clinic A's kiosk; the adult's intake has started."""
    body = {
        "device_id": str(clinics.devices[("a", "kiosk")]),
        "device_type": "kiosk",
        "device_secret": DEVICE_SECRET,
    }
    device = client.post("/v1/auth/device-token", json=body)
    assert device.status_code == 200, device.text
    checkin = {
        "clinic_id": str(clinics.a),
        "kiosk_id": str(clinics.devices[("a", "kiosk")]),
        "digits": ROUTINE_DIGITS,
        "language": "en",
    }
    started = client.post(
        "/v1/checkin/keypad", json=checkin, headers=bearer(device.json()["access_token"])
    )
    assert started.status_code == 200, started.text
    session_id, token = started.json()["session_id"], started.json()["session_token"]
    said = client.post(
        f"/v1/sessions/{session_id}/patient", json={"patient_type": "adult"}, headers=bearer(token)
    )
    assert said.status_code in (200, 204), said.text
    return str(session_id)


@pytest.fixture(scope="module")
def visits(flow: TestClient, clinics: Any) -> list[str]:
    """Four open visits of clinic A: two without a token, one waiting, one waiting behind it."""
    ids = [check_in(flow, clinics) for _ in range(4)]
    queue = cast(QueueService, flow.app.state.queue)  # type: ignore[attr-defined]

    async def assign() -> None:
        for session_id, level in ((ids[2], 3), (ids[3], 4)):
            async with clinic_session(clinics.a) as db:
                await queue.assign_token(
                    db, clinic_id=clinics.a, session_id=session_id, level=level
                )

    flow.portal.call(assign)  # type: ignore[union-attr]
    return ids


def test_the_worklist_reads_each_session_once_and_every_token_at_once(
    flow: TestClient, clinics: Any, visits: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = cast(IntakeRuntime, flow.app.state.intake)  # type: ignore[attr-defined]
    queue = cast(QueueService, flow.app.state.queue)  # type: ignore[attr-defined]
    calls = {"token one by one": 0, "tokens at once": 0}
    reads: Counter[str] = Counter()
    read_state = runtime.graph.aget_state
    one_token = queue.session_token
    many_tokens = queue.session_tokens

    async def counted_state(config: Any, *args: Any, **kwargs: Any) -> Any:
        reads[config["configurable"]["thread_id"]] += 1
        return await read_state(config, *args, **kwargs)

    async def counted_token(*args: Any, **kwargs: Any) -> Any:
        calls["token one by one"] += 1
        return await one_token(*args, **kwargs)

    async def counted_tokens(*args: Any, **kwargs: Any) -> Any:
        calls["tokens at once"] += 1
        return await many_tokens(*args, **kwargs)

    monkeypatch.setattr(runtime.graph, "aget_state", counted_state)
    monkeypatch.setattr(queue, "session_token", counted_token)
    monkeypatch.setattr(queue, "session_tokens", counted_tokens)
    response = flow.get("/v1/nurse/worklist", headers=staff(flow, clinics))
    assert response.status_code == 200, response.text
    listed = [item["session_id"] for item in response.json()]
    assert set(visits) <= set(listed)
    # Each open session's checkpoint is read once (twice before SHE-325), including the clinic
    # fixture's session that has none, and one query finds every token.
    assert set(listed) <= set(reads)
    assert set(reads.values()) == {1}
    assert calls == {"token one by one": 0, "tokens at once": 1}


def test_the_worklist_says_what_the_one_by_one_reads_said(
    flow: TestClient, clinics: Any, visits: list[str]
) -> None:
    """The route before SHE-325, as the oracle: state, pause and token read session by session."""
    runtime = cast(IntakeRuntime, flow.app.state.intake)  # type: ignore[attr-defined]
    queue = cast(QueueService, flow.app.state.queue)  # type: ignore[attr-defined]
    response = flow.get("/v1/nurse/worklist", headers=staff(flow, clinics))
    assert response.status_code == 200, response.text
    listed = response.json()
    ids = [item["session_id"] for item in listed]

    async def one_by_one() -> list[dict[str, Any]]:
        presses = await feels_worse_summaries(clinics.a, ids)
        questions = await medical_questions(clinics.a, ids)
        expected: list[dict[str, Any]] = []
        for item in listed:
            session_id = item["session_id"]
            state = await runtime.state(session_id)
            assert state is not None
            waiting, _ = await runtime.waiting_for(session_id)
            async with clinic_session(clinics.a) as db:
                token = await queue.session_token(db, session_id=session_id)
            built = worklist_item(
                state,
                patient_display=item["patient_display"],
                waiting=waiting,
                token_code=token.token_code if token else None,
                token_status=token.status.value if token else None,
                feels_worse=presses.get(session_id),
                medical_questions=questions.get(session_id),
                queue_token=token,
            )
            expected.append(built.model_dump(mode="json", exclude_unset=True))
        return expected

    assert listed == flow.portal.call(one_by_one)  # type: ignore[union-attr]
    tokens = {item["session_id"]: item.get("token_code") for item in listed}
    assert tokens[visits[0]] is None and tokens[visits[1]] is None
    assert tokens[visits[2]] and tokens[visits[3]]  # the batched lookup found both
