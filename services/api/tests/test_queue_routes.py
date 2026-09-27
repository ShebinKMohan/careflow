"""Queue routes (M1-10): roles, the public board's fields, errors and the session's own token.

Needs Postgres (`make up`); skipped without it.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
import yaml
from fastapi import FastAPI
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.engine import Engine

from careflow_api.events.streams import xrange
from careflow_api.queue.rooms import rooms
from careflow_api.queue.service import QueueService
from careflow_api.rls import clinic_session
from careflow_api.security import Role, create_access_token
from careflow_contracts.testing import CONTRACTS_DIR

SPEC: dict[str, Any] = yaml.safe_load((CONTRACTS_DIR / "openapi.yaml").read_text(encoding="utf-8"))
T0 = datetime(2026, 9, 26, 9, 0, tzinfo=UTC)


def bearer(role: Role, clinic_id: uuid.UUID, session_id: str | None = None) -> dict[str, str]:
    extra = {"session_id": session_id or "ses_test"} if role == "patient_session" else {}
    token, _ = create_access_token(
        secret=os.environ["JWT_SECRET"],
        subject=f"sub-{role}",
        role=role,
        clinic_id=clinic_id,
        extra=extra,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def clinic(database: Engine) -> uuid.UUID:
    clinic_id = uuid.uuid4()
    with database.begin() as connection:
        connection.execute(
            text("INSERT INTO clinic (id, name, timezone) VALUES (:id, :name, 'Asia/Dubai')"),
            {"id": clinic_id, "name": f"queue-routes-{clinic_id.hex[:8]}"},
        )
    return clinic_id


async def queued(
    database: Engine, app: FastAPI, clinic_id: uuid.UUID, levels: list[int]
) -> list[str]:
    """Sessions queued at `levels` through the app's own QueueService."""
    ids = [uuid.uuid4() for _ in levels]
    with database.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.clinic_id', :c, true)"), {"c": str(clinic_id)}
        )
        for i, session_id in enumerate(ids):
            connection.execute(
                text(
                    "INSERT INTO patient_session (id, clinic_id, language, started_at, expires_at) "
                    "VALUES (:id, :c, 'en', :at, :at + interval '2 hours')"
                ),
                {"id": session_id, "c": clinic_id, "at": T0 + timedelta(minutes=i)},
            )
    service = cast(QueueService, app.state.queue)
    for session_id, level in zip(ids, levels, strict=True):
        async with clinic_session(clinic_id) as db:
            await service.assign_token(
                db, clinic_id=clinic_id, session_id=str(session_id), level=level
            )
    return [str(i) for i in ids]


@pytest.mark.parametrize(
    ("method", "path", "allowed"),
    [
        ("post", "/v1/queue/emergency/call-next", {"nurse"}),
        ("post", f"/v1/queue/tokens/{uuid.uuid4()}/arrived", {"nurse"}),
        ("post", f"/v1/queue/tokens/{uuid.uuid4()}/no-show", {"nurse"}),
        ("post", f"/v1/queue/tokens/{uuid.uuid4()}/close", {"nurse"}),
        ("get", "/v1/board/emergency", {"board"}),
    ],
)
async def test_only_the_contract_roles_may_call(
    client: AsyncClient,
    database: Engine,
    clinic: uuid.UUID,
    method: str,
    path: str,
    allowed: set[str],
) -> None:
    assert (await client.request(method, path)).status_code == 401
    for role in (
        "kiosk_device",
        "patient_session",
        "nurse",
        "doctor",
        "admin",
        "board",
        "integration",
    ):
        response = await client.request(method, path, headers=bearer(cast(Role, role), clinic))
        assert (response.status_code == 403) is (role not in allowed), (role, response.text)


async def test_the_board_shows_only_token_code_room_and_status(
    app: FastAPI, client: AsyncClient, database: Engine, clinic: uuid.UUID
) -> None:
    await queued(database, app, clinic, [3, 2, 4])  # A-001 (3), A-002 (2), G-001 (4)
    nurse, board = bearer("nurse", clinic), bearer("board", clinic)
    called = await client.post("/v1/queue/emergency/call-next", headers=nurse)
    assert called.status_code == 200 and called.json()["token"]["token_code"] == "A-002"
    response = await client.get("/v1/board/emergency", headers=board)
    assert response.status_code == 200
    assert response.json() == [
        {"token_code": "A-002", "room": "Resus 1", "status": "called"},
        {"token_code": "A-001", "room": None, "status": "waiting"},
    ]
    allowed = set(SPEC["components"]["schemas"]["BoardEntry"]["properties"])
    assert allowed == {"token_code", "room", "status"}
    assert SPEC["components"]["schemas"]["BoardEntry"]["additionalProperties"] is False
    for entry in response.json():
        assert set(entry) == allowed  # no name, level, session or anything else
    assert (await client.get("/v1/board/general", headers=board)).json() == [
        {"token_code": "G-001", "room": None, "status": "waiting"}
    ]


async def test_errors_are_problems(
    app: FastAPI, client: AsyncClient, database: Engine, clinic: uuid.UUID
) -> None:
    await queued(database, app, clinic, [3, 3])
    nurse = bearer("nurse", clinic)
    assert (await client.post("/v1/queue/nowhere/call-next", headers=nurse)).status_code == 404
    assert (
        await client.get("/v1/board/nowhere", headers=bearer("board", clinic))
    ).status_code == 404
    assert (
        await client.post("/v1/queue/emergency/call-next?room=nope", headers=nurse)
    ).status_code == 404
    first = await client.post("/v1/queue/emergency/call-next?room=ed-1", headers=nurse)
    assert first.status_code == 200 and first.json()["token"]["room"] == "ED 1"
    busy = await client.post("/v1/queue/emergency/call-next?room=ed-1", headers=nurse)
    assert busy.status_code == 409
    assert busy.headers["content-type"].startswith("application/problem+json")
    token_id = first.json()["token"]["token_id"]
    arrived = await client.post(f"/v1/queue/tokens/{token_id}/arrived", headers=nurse)
    assert arrived.status_code == 200 and arrived.json()["status"] == "arrived"
    assert (
        await client.post(f"/v1/queue/tokens/{token_id}/arrived", headers=nurse)
    ).status_code == 409
    closed = await client.post(f"/v1/queue/tokens/{token_id}/close", headers=nurse)
    assert closed.status_code == 200 and closed.json()["status"] == "closed"
    unknown = await client.post(f"/v1/queue/tokens/{uuid.uuid4()}/close", headers=nurse)
    assert unknown.status_code == 404
    empty = await client.post("/v1/queue/general/call-next", headers=nurse)
    assert empty.status_code == 200 and empty.json() == {"token": None}


async def test_a_patient_session_reads_only_its_own_token(
    app: FastAPI, client: AsyncClient, database: Engine, clinic: uuid.UUID
) -> None:
    mine, theirs = await queued(database, app, clinic, [3, 4])
    own = await client.get(
        f"/v1/sessions/{mine}/token", headers=bearer("patient_session", clinic, mine)
    )
    assert own.status_code == 200
    assert {k: own.json()[k] for k in ("token_code", "department", "status")} == {
        "token_code": "A-001",
        "department": "emergency",
        "status": "waiting",
    }
    other = await client.get(
        f"/v1/sessions/{theirs}/token", headers=bearer("patient_session", clinic, mine)
    )
    assert other.status_code == 403
    nurse = await client.get(f"/v1/sessions/{theirs}/token", headers=bearer("nurse", clinic))
    assert nurse.status_code == 200 and nurse.json()["token_code"] == "G-001"
    missing = await client.get(
        f"/v1/sessions/{uuid.uuid4()}/token", headers=bearer("nurse", clinic)
    )
    assert missing.status_code == 404


async def test_the_app_validates_the_rooms_file_at_start(app: FastAPI) -> None:
    assert cast(QueueService, app.state.queue).rooms == rooms()


async def test_queue_frames_go_to_the_tests_own_stream(
    app: FastAPI,
    client: AsyncClient,
    database: Engine,
    clinic: uuid.UUID,
    redis_client: Redis,
    stream: str,
) -> None:
    """`queue.status` frames skip the outbox: they go straight to `settings.event_stream`. Tests
    once sent them to the dev `careflow:events`, where the dev API's consumer group retried their
    unknown clinics forever (ADR-043)."""
    await queued(database, app, clinic, [3, 2])
    called = await client.post("/v1/queue/emergency/call-next", headers=bearer("nurse", clinic))
    assert called.status_code == 200
    deadline = time.monotonic() + 5
    while len(frames := await xrange(redis_client, stream)) < 2:  # sent after the commit
        assert time.monotonic() < deadline, "no queue.status frames in the test's own stream"
        await asyncio.sleep(0.05)
    assert [fields["type"] for _, fields in frames] == ["queue.status", "queue.status"]
    assert {fields["clinic_id"] for _, fields in frames} == {str(clinic)}
