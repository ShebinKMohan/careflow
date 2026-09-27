"""Live events over WebSockets (M1-11): tickets, latency, filtering, heartbeat and replay.

A real uvicorn server runs the app in-process (lifespan on: the outbox relay and the live hub), on
Postgres and Valkey; the client is `websockets`. Skipped without them, unless CI requires them.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import httpx
import pytest
import uvicorn
from redis.asyncio import Redis
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from careflow_api.events.outbox import OutboxPublisher
from careflow_api.events.publisher import Event
from careflow_api.rls import clinic_session
from careflow_api.security import Role, create_access_token
from careflow_contracts.events import AlertRaisedData, TokenCalledData

NOW = datetime(2026, 9, 26, 9, 8, tzinfo=UTC)


@dataclass
class Live:
    port: int
    stream: str

    @property
    def http(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def ws(self) -> str:
        return f"ws://127.0.0.1:{self.port}"


@pytest.fixture
async def live(
    monkeypatch: pytest.MonkeyPatch, database: object, redis_client: Redis
) -> AsyncIterator[Live]:
    stream = f"careflow:test:{uuid.uuid4().hex}"
    monkeypatch.setenv("CAREFLOW_EVENT_STREAM", stream)
    monkeypatch.setenv("CAREFLOW_WS_HEARTBEAT_S", "0.5")
    monkeypatch.setenv("CAREFLOW_QUEUE_GRACE_TASK", "0")
    from careflow_api import db
    from careflow_api.config import get_settings
    from careflow_api.main import create_app

    for cached in (get_settings, db.get_engine, db.get_sessionmaker):
        cached.cache_clear()
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=0, log_level="warning", ws="auto")
    )
    task = asyncio.create_task(server.serve())
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline or task.done():
            raise RuntimeError("the test server did not start")
        await asyncio.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield Live(port, stream)
    server.should_exit = True
    await task
    await redis_client.delete(stream)
    await db.get_engine().dispose()
    for cached in (get_settings, db.get_engine, db.get_sessionmaker):
        cached.cache_clear()


def bearer(role: Role, clinic_id: uuid.UUID, session_id: str | None = None) -> dict[str, str]:
    extra = {"session_id": session_id} if session_id else {}
    token, _ = create_access_token(
        secret=os.environ["JWT_SECRET"],
        subject=f"sub-{role}",
        role=role,
        clinic_id=clinic_id,
        extra=extra,
    )
    return {"Authorization": f"Bearer {token}"}


async def ticket(
    live: Live, role: Role, clinic_id: uuid.UUID, session_id: str | None = None
) -> str:
    body: dict[str, Any] = (
        {"channel": "session", "session_id": session_id} if session_id else {"channel": "staff"}
    )
    headers = bearer(role, clinic_id, session_id if role == "patient_session" else None)
    async with httpx.AsyncClient(base_url=live.http) as client:
        response = await client.post("/v1/ws-ticket", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return cast(str, response.json()["ticket"])


async def publish(event: Event) -> None:
    async with clinic_session(uuid.UUID(event.clinic_id)) as db:
        await OutboxPublisher().add(db, event)


def alert(clinic_id: uuid.UUID, session_id: str) -> Event:
    alert_body = {
        "alert_id": f"alr_{uuid.uuid4().hex[:12]}",
        "session_id": session_id,
        "rule_id": "CHEST_PAIN_ECG_10MIN",
        "ruleset_version": "2026.1.0",
        "severity": "critical",
        "evidence_fact_ids": ["f_01M3EF2PM000000000035FW001"],
        "required_action": "ecg_within_10_min",
        "display_text": "Chest pain: ECG within 10 minutes of arrival.",
        "partial": False,
        "missing_inputs": [],
        "raised_at": NOW.isoformat(),
    }
    data = AlertRaisedData.model_validate(
        {"clinic_id": str(clinic_id), "session_id": session_id, "alert": alert_body}
    )
    return Event("alert.raised", data, NOW)


def called(clinic_id: uuid.UUID, session_id: str) -> Event:
    data = TokenCalledData(
        clinic_id=str(clinic_id),
        session_id=session_id,
        token_id=f"tok_{session_id}",
        token_code="A-014",
        department="emergency",
        room="Resus 2",
        called_at=NOW,
    )
    return Event("token.called", data, NOW)


async def next_event(socket: ClientConnection, limit_s: float = 5.0) -> dict[str, Any]:
    """The next non-heartbeat frame."""
    deadline = time.monotonic() + limit_s
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("no event")
        message = json.loads(await asyncio.wait_for(socket.recv(), timeout=remaining))
        if message["type"] != "heartbeat":
            return message


async def events_within(socket: ClientConnection, seconds: float) -> list[dict[str, Any]]:
    received: list[dict[str, Any]] = []
    deadline = time.monotonic() + seconds
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            message = json.loads(await asyncio.wait_for(socket.recv(), timeout=remaining))
        except TimeoutError:
            break
        if message["type"] != "heartbeat":
            received.append(message)
    return received


async def test_an_alert_reaches_a_nurse_in_under_a_second_at_p95(
    live: Live, new_clinic: Callable[[], uuid.UUID]
) -> None:
    clinic = new_clinic()
    latencies: list[float] = []
    async with connect(
        f"{live.ws}/v1/ws/staff?ticket={await ticket(live, 'nurse', clinic)}"
    ) as socket:
        await asyncio.sleep(0.3)  # the hub reads from `$`: let the connection settle
        for n in range(50):
            event = alert(clinic, f"ses_{n}")
            started = time.monotonic()
            await publish(event)
            message = await next_event(socket)
            latencies.append(time.monotonic() - started)
            assert message["type"] == "alert.raised" and message["data"]["session_id"] == f"ses_{n}"
            assert set(message) == {"id", "type", "timestamp", "data"}
    latencies.sort()
    p95 = latencies[int(0.95 * len(latencies)) - 1]
    print(
        f"alert.raised commit->nurse socket over 50 runs: p50 {latencies[24] * 1000:.0f} ms, "
        f"p95 {p95 * 1000:.0f} ms, max {latencies[-1] * 1000:.0f} ms"
    )
    assert p95 < 1.0


async def test_another_clinic_hears_nothing_and_a_patient_never_hears_an_alert(
    live: Live, new_clinic: Callable[[], uuid.UUID]
) -> None:
    clinic, other = new_clinic(), new_clinic()
    session = "ses_patient_1"
    nurse_ticket = await ticket(live, "nurse", clinic)
    other_ticket = await ticket(live, "nurse", other)
    patient_ticket = await ticket(live, "patient_session", clinic, session)
    async with (
        connect(f"{live.ws}/v1/ws/staff?ticket={nurse_ticket}") as nurse,
        connect(f"{live.ws}/v1/ws/staff?ticket={other_ticket}") as stranger,
        connect(f"{live.ws}/v1/ws/sessions/{session}?ticket={patient_ticket}") as patient,
    ):
        await asyncio.sleep(0.3)
        await publish(alert(clinic, session))
        await publish(called(clinic, session))
        await publish(called(clinic, "ses_someone_else"))
        nurse_saw = await events_within(nurse, 1.5)
        stranger_saw = await events_within(stranger, 1.0)
        patient_saw = await events_within(patient, 1.0)
    assert [m["type"] for m in nurse_saw] == ["alert.raised", "token.called", "token.called"]
    assert stranger_saw == []
    assert [(m["type"], m["data"]["session_id"]) for m in patient_saw] == [
        ("token.called", session)
    ]


async def test_a_ticket_works_once_and_only_for_its_channel(
    live: Live, new_clinic: Callable[[], uuid.UUID]
) -> None:
    clinic = new_clinic()
    once = await ticket(live, "nurse", clinic)
    async with connect(f"{live.ws}/v1/ws/staff?ticket={once}"):
        await asyncio.sleep(0.1)
    async with connect(f"{live.ws}/v1/ws/staff?ticket={once}") as reused:
        with pytest.raises(ConnectionClosed) as closed:
            await reused.recv()
    assert closed.value.rcvd is not None and closed.value.rcvd.code == 4401
    async with connect(f"{live.ws}/v1/ws/staff") as missing:
        with pytest.raises(ConnectionClosed) as closed:
            await missing.recv()
    assert closed.value.rcvd is not None and closed.value.rcvd.code == 4401
    staff = await ticket(live, "nurse", clinic)
    async with connect(f"{live.ws}/v1/ws/sessions/ses_x?ticket={staff}") as wrong:
        with pytest.raises(ConnectionClosed) as closed:
            await wrong.recv()
    assert closed.value.rcvd is not None and closed.value.rcvd.code == 4403
    async with httpx.AsyncClient(base_url=live.http) as client:
        board_session = await client.post(
            "/v1/ws-ticket",
            json={"channel": "session", "session_id": "ses_x"},
            headers=bearer("board", clinic),
        )
        patient_staff = await client.post(
            "/v1/ws-ticket",
            json={"channel": "staff"},
            headers=bearer("patient_session", clinic, "ses_x"),
        )
        other_session = await client.post(
            "/v1/ws-ticket",
            json={"channel": "session", "session_id": "ses_y"},
            headers=bearer("patient_session", clinic, "ses_x"),
        )
        no_session = await client.post(
            "/v1/ws-ticket", json={"channel": "session"}, headers=bearer("kiosk_device", clinic)
        )
        anonymous = await client.post("/v1/ws-ticket", json={"channel": "staff"})
    assert [
        r.status_code for r in (board_session, patient_staff, other_session, no_session, anonymous)
    ] == [
        403,
        403,
        403,
        422,
        401,
    ]


async def test_heartbeats_and_replay_after_a_reconnect(
    live: Live, new_clinic: Callable[[], uuid.UUID]
) -> None:
    clinic = new_clinic()
    async with connect(
        f"{live.ws}/v1/ws/staff?ticket={await ticket(live, 'doctor', clinic)}"
    ) as socket:
        heartbeat = json.loads(await asyncio.wait_for(socket.recv(), timeout=3))
        assert heartbeat["type"] == "heartbeat"  # 0.5 s of silence in this test
    async with connect(
        f"{live.ws}/v1/ws/staff?ticket={await ticket(live, 'nurse', clinic)}"
    ) as socket:
        await asyncio.sleep(0.3)
        await publish(called(clinic, "ses_1"))
        seen = await next_event(socket)
    await publish(called(clinic, "ses_2"))  # while the nurse is away
    await publish(called(clinic, "ses_3"))
    await asyncio.sleep(0.5)  # the relay publishes them
    fresh = await ticket(live, "nurse", clinic)
    url = f"{live.ws}/v1/ws/staff?ticket={fresh}&last_event_id={seen['id']}"
    async with connect(url) as socket:
        replayed = [await next_event(socket), await next_event(socket)]
    assert [m["data"]["session_id"] for m in replayed] == ["ses_2", "ses_3"]
