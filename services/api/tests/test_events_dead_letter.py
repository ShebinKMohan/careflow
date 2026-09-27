"""Poison entries end in the dead-letter stream; other failures keep being retried (ADR-043).

A poison entry fails the same way on every delivery: its clinic does not exist (the incident:
`queue.status` frames that tests left in the dev stream, whose clinics exist only in
careflow_test), or it is not a CareFlow event at all. The consumers here re-deliver every ~50 ms
instead of every 30 s. Needs Postgres and Valkey (`make up`); skipped without them.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DataError, IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from careflow_api.events.bus import BusEvent, EventBus, MalformedEntry, is_poison
from careflow_api.events.frames import QUEUE_STATUS, publish_frame
from careflow_api.events.streams import xadd, xrange
from careflow_api.models import ProcessedEvent
from careflow_api.rls import clinic_session

GROUP = "test-dead-letter"
NOW = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)


async def wait_for(condition: Callable[[], Awaitable[bool]], what: str) -> None:
    deadline = time.monotonic() + 10
    while not await condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.02)


def fast_bus(redis: Redis, stream: str, *, max_deliveries: int) -> EventBus:
    return EventBus(
        redis, stream=stream, block_ms=50, claim_idle_ms=50, max_deliveries=max_deliveries
    )


async def queue_frame(redis: Redis, stream: str, clinic_id: uuid.UUID) -> str:
    """A `queue.status` frame, as `QueueService.broadcast` sends it."""
    return await publish_frame(
        redis,
        frame_type=QUEUE_STATUS,
        clinic_id=str(clinic_id),
        session_id=str(uuid.uuid4()),
        data={"state": "waiting", "position": 1, "wait_estimate_minutes": 5},
        now=NOW,
        stream=stream,
    )


async def pending(redis: Redis, stream: str) -> int:
    return int((await redis.xpending(stream, GROUP))["pending"])


def dead_lettered(redis: Redis, bus: EventBus, count: int) -> Callable[[], Awaitable[bool]]:
    """`count` entries in the dead-letter stream and none left pending."""

    async def check() -> bool:
        return await redis.xlen(bus.dead_stream) == count and await pending(redis, bus.stream) == 0

    return check


async def never(db: AsyncSession, event: BusEvent) -> None:
    raise AssertionError("a poison entry reached the handler")


async def stop(consumer: asyncio.Task[None]) -> None:
    assert not consumer.done(), "the consumer loop died"
    consumer.cancel()
    await asyncio.gather(consumer, return_exceptions=True)


@pytest.mark.parametrize(
    ("exc", "poison"),
    [
        (MalformedEntry("KeyError: 'id'"), True),
        (IntegrityError("INSERT", {}, Exception("foreign key violation")), True),
        (DataError("INSERT", {}, Exception("value too long")), True),
        (OperationalError("SELECT", {}, Exception("connection refused")), False),
        (RedisConnectionError("Connection refused"), False),
        (TimeoutError(), False),
        (RuntimeError("the handler's own error"), False),
    ],
    ids=["malformed", "integrity", "data", "db-down", "redis-down", "timeout", "handler-bug"],
)
def test_only_a_fault_of_the_entry_itself_is_poison(exc: Exception, poison: bool) -> None:
    assert is_poison(exc) is poison


async def test_an_unknown_clinic_is_dead_lettered_after_max_deliveries(
    app: FastAPI,
    database: Engine,
    redis_client: Redis,
    stream: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The incident: every delivery broke the `processed_events` foreign key, and the entry was
    retried every 30 s, forever."""
    caplog.set_level(logging.ERROR, logger="careflow_api.events")
    bus = fast_bus(redis_client, stream, max_deliveries=3)
    await bus.ensure_group(GROUP)
    unknown = uuid.uuid4()
    stream_id = await queue_frame(redis_client, stream, unknown)
    consumer = bus.subscribe(GROUP, never)
    await wait_for(dead_lettered(redis_client, bus, 1), "the dead letter")
    await asyncio.sleep(0.3)  # several more claim rounds: it is not delivered again
    await stop(consumer)

    [(_, original)] = await xrange(redis_client, stream)
    [(_, dead)] = await xrange(redis_client, bus.dead_stream)
    assert {key: dead[key] for key in original} == original  # the entry as it was
    assert (dead["source_stream"], dead["source_id"]) == (stream, stream_id)
    assert (dead["group"], dead["consumer"], dead["deliveries"]) == (GROUP, bus.consumer, "3")
    assert dead["error"].startswith(
        'IntegrityError: insert or update on table "processed_events" violates foreign key '
        'constraint "processed_events_clinic_id_fkey"'
    )
    # Under row-level security Postgres leaves the key out of the DETAIL; the dead letter still
    # names the clinic, in the entry's own fields.
    assert dead["clinic_id"] == str(unknown)
    assert "INSERT INTO" not in dead["error"]  # the driver's message, not the statement
    assert datetime.fromisoformat(dead["dead_at"]).tzinfo is not None
    assert await redis_client.xlen(bus.dead_stream) == 1
    assert await pending(redis_client, stream) == 0
    messages = [r.getMessage() for r in caplog.records if r.name == "careflow_api.events"]
    assert messages.count("events.handler_failed") == 3  # one per delivery, then no more
    assert messages.count("events.dead_lettered") == 1


async def test_an_entry_that_is_not_an_event_is_dead_lettered(
    redis_client: Redis, stream: str
) -> None:
    bus = fast_bus(redis_client, stream, max_deliveries=2)
    await bus.ensure_group(GROUP)
    await xadd(redis_client, stream, {"hello": "world"}, maxlen=100)
    consumer = bus.subscribe(GROUP, never)
    await wait_for(dead_lettered(redis_client, bus, 1), "the dead letter")
    await stop(consumer)

    [(_, dead)] = await xrange(redis_client, bus.dead_stream)
    assert dead["hello"] == "world"
    assert (dead["deliveries"], dead["error"]) == ("2", "MalformedEntry: KeyError: 'id'")


@pytest.mark.parametrize(
    ("failure", "failures", "max_deliveries"),
    [
        (lambda: OperationalError("SELECT", {}, Exception("the database is down")), 5, 2),
        (lambda: IntegrityError("INSERT", {}, Exception("a concurrent twin")), 2, 3),
    ],
    ids=["transient-past-max-deliveries", "integrity-error-that-clears-in-time"],
)
async def test_a_failure_that_clears_is_handled_once_and_never_dead_lettered(
    app: FastAPI,
    new_clinic: Callable[[], uuid.UUID],
    redis_client: Redis,
    stream: str,
    failure: Callable[[], Exception],
    failures: int,
    max_deliveries: int,
) -> None:
    """At-least-once holds for transient failures however long they last; an integrity error
    that clears (a race) before `max_deliveries` is handled too."""
    clinic = new_clinic()
    bus = fast_bus(redis_client, stream, max_deliveries=max_deliveries)
    await bus.ensure_group(GROUP)
    await queue_frame(redis_client, stream, clinic)
    attempts: list[uuid.UUID] = []
    handled: list[uuid.UUID] = []

    async def flaky(db: AsyncSession, event: BusEvent) -> None:
        attempts.append(event.event_id)
        if len(attempts) <= failures:
            raise failure()
        handled.append(event.event_id)

    async def done() -> bool:
        return bool(handled) and await pending(redis_client, stream) == 0

    consumer = bus.subscribe(GROUP, flaky)
    await wait_for(done, "the handler to succeed")
    await asyncio.sleep(0.3)  # several more claim rounds: it is not handled again
    await stop(consumer)

    assert (len(attempts), len(handled)) == (failures + 1, 1)
    assert not await redis_client.exists(bus.dead_stream)
    async with clinic_session(clinic) as db:
        query = select(func.count()).select_from(ProcessedEvent)
        assert (await db.execute(query)).scalar_one() == 1


@pytest.mark.parametrize(("command", "deliveries"), [("xadd", ["2"]), ("xack", ["1", "2"])])
async def test_a_redis_failure_while_dead_lettering_loses_nothing(
    redis_client: Redis,
    stream: str,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    deliveries: list[str],
) -> None:
    """A failed dead-letter XADD leaves the entry pending; a failed XACK after it lets the next
    delivery dead-letter it again. Twice there at worst, never lost, and the consumer goes on."""
    bus = fast_bus(redis_client, stream, max_deliveries=1)
    await bus.ensure_group(GROUP)
    await xadd(redis_client, stream, {"hello": "world"}, maxlen=100)
    real = getattr(redis_client, command)
    failed: list[str] = []

    async def fail_once(*args: Any, **kwargs: Any) -> Any:
        if not failed:
            failed.append(command)
            raise RedisConnectionError("Redis went away")
        return await real(*args, **kwargs)

    monkeypatch.setattr(redis_client, command, fail_once)
    consumer = bus.subscribe(GROUP, never)
    await wait_for(dead_lettered(redis_client, bus, len(deliveries)), "the dead letters")
    await stop(consumer)

    assert failed == [command]
    dead = await xrange(redis_client, bus.dead_stream)
    assert [fields["deliveries"] for _, fields in dead] == deliveries
