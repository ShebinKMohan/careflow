"""The transactional outbox and the relay (M1-11), on Postgres and Valkey.

Skipped without them (`make up`), unless CI requires them.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
import yaml
from fastapi import FastAPI
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from careflow_api.events.outbox import SCHEMA_VERSION, OutboxPublisher
from careflow_api.events.publisher import Event
from careflow_api.events.relay import OutboxRelay
from careflow_api.events.streams import xrange
from careflow_api.models import OutboxEvent
from careflow_api.rls import clinic_session
from careflow_contracts.events import TokenCalledData, TokenNoShowData
from careflow_contracts.testing import CONTRACTS_DIR

NOW = datetime(2026, 9, 26, 9, 8, tzinfo=UTC)


def called(clinic_id: uuid.UUID, n: int = 1, *, key: str | None = None) -> Event:
    data = TokenCalledData(
        clinic_id=str(clinic_id),
        session_id=f"ses_{n}",
        token_id=f"tok_{n}",
        token_code=f"A-{n % 999 + 1:03d}",
        department="emergency",
        room="Resus 1",
        called_at=NOW,
    )
    return Event("token.called", data, NOW, idempotency_key=key)


async def rows(clinic_id: uuid.UUID) -> list[OutboxEvent]:
    async with clinic_session(clinic_id) as db:
        return list((await db.execute(select(OutboxEvent).order_by(OutboxEvent.seq))).scalars())


async def test_add_writes_one_validated_row_per_idempotency_key(
    app: FastAPI, new_clinic: Callable[[], uuid.UUID]
) -> None:
    clinic = new_clinic()
    publisher = OutboxPublisher()
    async with clinic_session(clinic) as db:
        await publisher.add(db, called(clinic, key="ses_1:token.called"))
        await publisher.add(db, called(clinic, key="ses_1:token.called"))  # a retry: ignored
        await publisher.add(db, called(clinic, 2))
    [first, second] = await rows(clinic)
    assert (first.event_type, first.schema_version, first.session_id) == (
        "token.called",
        "0.1.0",
        "ses_1",
    )
    assert first.payload == called(clinic).envelope()
    assert first.published_at is None and first.attempts == 0
    assert second.idempotency_key != first.idempotency_key  # no key given: a fresh one


async def test_invalid_events_are_refused(
    app: FastAPI, new_clinic: Callable[[], uuid.UUID]
) -> None:
    clinic, other = new_clinic(), new_clinic()
    publisher = OutboxPublisher()
    async with clinic_session(clinic) as db:
        with pytest.raises(ValueError, match="unknown event type"):
            await publisher.add(db, Event("token.teleported", called(clinic).data, NOW))
        no_show = TokenNoShowData(
            clinic_id=str(clinic),
            session_id="ses_1",
            token_id="tok_1",
            token_code="A-001",
            department="emergency",
            marked_at=NOW,
        )
        with pytest.raises(ValidationError):  # the data of another event type
            await publisher.add(db, Event("token.called", no_show, NOW))
    with pytest.raises(DBAPIError):  # RLS: only the caller's clinic may be written
        async with clinic_session(clinic) as db:
            await publisher.add(db, called(other))
    assert await rows(clinic) == [] and await rows(other) == []


async def test_an_event_in_a_rolled_back_transaction_is_never_published(
    app: FastAPI, new_clinic: Callable[[], uuid.UUID], redis_client: Redis, stream: str
) -> None:
    clinic = new_clinic()
    with pytest.raises(RuntimeError, match="the change failed"):
        async with clinic_session(clinic) as db:
            await OutboxPublisher().add(db, called(clinic))
            raise RuntimeError("the change failed")
    relay = OutboxRelay(redis_client, stream=stream, clinic_ids=[clinic])
    assert await relay.run_once() == 0
    assert await redis_client.xlen(stream) == 0 and await rows(clinic) == []


async def test_the_relay_publishes_in_order_and_stamps_each_row(
    app: FastAPI, new_clinic: Callable[[], uuid.UUID], redis_client: Redis, stream: str
) -> None:
    clinic = new_clinic()
    publisher = OutboxPublisher()
    async with clinic_session(clinic) as db:
        for n in range(1, 6):
            await publisher.add(db, called(clinic, n))
    async with clinic_session(clinic) as db:
        for n in range(6, 9):
            await publisher.add(db, called(clinic, n))
    relay = OutboxRelay(redis_client, stream=stream, clinic_ids=[clinic], batch=5)
    assert await relay.run_once() == 5  # one batch per clinic per pass
    assert await relay.run_once() == 3
    assert await relay.run_once() == 0
    entries = await xrange(redis_client, stream)
    stored = await rows(clinic)
    assert [fields["id"] for _, fields in entries] == [str(r.id) for r in stored]
    assert [json.loads(f["payload"])["data"]["session_id"] for _, f in entries] == [
        f"ses_{n}" for n in range(1, 9)
    ]
    assert entries[0][1]["clinic_id"] == str(clinic) and entries[0][1]["session_id"] == "ses_1"
    assert all(r.published_at is not None and r.attempts == 1 for r in stored)


async def test_with_redis_down_events_stay_in_the_outbox(
    app: FastAPI, new_clinic: Callable[[], uuid.UUID]
) -> None:
    clinic = new_clinic()
    async with clinic_session(clinic) as db:
        await OutboxPublisher().add(db, called(clinic))
    down = Redis.from_url(
        "redis://127.0.0.1:1/0", decode_responses=True, socket_connect_timeout=0.5
    )
    try:
        assert await OutboxRelay(down, stream="unused", clinic_ids=[clinic]).run_once() == 0
    finally:
        await down.aclose()
    [row] = await rows(clinic)
    assert (row.published_at, row.attempts, row.last_error) == (None, 1, "ConnectionError")


def test_the_outbox_schema_version_is_the_contract_version() -> None:
    spec = yaml.safe_load((CONTRACTS_DIR / "openapi.yaml").read_text(encoding="utf-8"))
    assert spec["info"]["version"] == SCHEMA_VERSION


async def test_every_row_is_counted_once(app: FastAPI, new_clinic: Callable[[], uuid.UUID]) -> None:
    clinic = new_clinic()
    async with clinic_session(clinic) as db:
        for n in range(10):
            await OutboxPublisher().add(db, called(clinic, n, key=f"k{n % 5}"))
    async with clinic_session(clinic) as db:
        count = (await db.execute(select(func.count()).select_from(OutboxEvent))).scalar_one()
    assert count == 5
