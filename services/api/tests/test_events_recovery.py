"""A consumer survives a Redis outage, and a failed FHIR write-back is never acknowledged (ADR-043).

The write-back consumer used to log a failed write and return, so the bus acknowledged the
`triage.confirmed` event and the write was never tried again. Now an EHR outage keeps the event
pending until the write succeeds, and a rejection goes to the dead-letter stream with its reason.
Needs Postgres and Valkey (`make up`); skipped without them.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from fastapi import FastAPI
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from careflow_api.events import bus as bus_module
from careflow_api.events.bus import (
    BusEvent,
    EventBus,
    PermanentFailure,
    TransientFailure,
    is_poison,
)
from careflow_api.events.streams import xadd, xrange
from careflow_api.intake.consumers import (
    WritebackRejected,
    WritebackUnavailable,
    writeback_consumer,
)
from careflow_api.models import ProcessedEvent
from careflow_api.rls import clinic_session
from careflow_integration.fhir.writeback.composition import DocumentOutcome
from careflow_integration.fhir.writeback.ports import WritebackOutcome, WritebackStatus

GROUP = "test-recovery"
NOW = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)


async def wait_for(condition: Callable[[], Awaitable[bool]], what: str) -> None:
    deadline = time.monotonic() + 10
    while not await condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.02)


def fast_bus(redis: Redis, stream: str, *, max_deliveries: int = 5) -> EventBus:
    return EventBus(
        redis, stream=stream, block_ms=50, claim_idle_ms=50, max_deliveries=max_deliveries
    )


async def stop(consumer: asyncio.Task[None]) -> None:
    assert not consumer.done(), "the consumer loop died"
    consumer.cancel()
    await asyncio.gather(consumer, return_exceptions=True)


async def confirmed(redis: Redis, stream: str, clinic: uuid.UUID) -> tuple[str, str]:
    """A `triage.confirmed` entry as the outbox relay writes it: (stream id, session id)."""
    session_id = str(uuid.uuid4())
    fields = {
        "id": str(uuid.uuid4()),
        "type": "triage.confirmed",
        "clinic_id": str(clinic),
        "session_id": session_id,
        "payload": json.dumps({"data": {"session_id": session_id}}),
        "created_at": NOW.isoformat(),
    }
    return await xadd(redis, stream, fields, maxlen=100), session_id


async def processed(clinic: uuid.UUID) -> int:
    async with clinic_session(clinic) as db:
        query = select(func.count()).select_from(ProcessedEvent)
        return int((await db.execute(query)).scalar_one())


async def pending(redis: Redis, stream: str) -> int:
    return int((await redis.xpending(stream, GROUP))["pending"])


class FakeRuntime:
    """`IntakeRuntime` as the consumer uses it: a session lock and the confirmed state."""

    @contextlib.asynccontextmanager
    async def serialized(self, session_id: str) -> AsyncIterator[None]:
        yield

    async def state(self, session_id: str) -> dict[str, Any]:
        return {"session_id": session_id, "nurse_decision": {"action": "confirm"}}


class FakeStore:
    def __init__(self) -> None:
        self.value: WritebackStatus | None = None

    async def status(self, clinic_id: str, session_id: str) -> WritebackStatus | None:
        return self.value


class FakeWriteback:
    """The `WritebackPort` with scripted outcomes: `retryable` failures, then a success."""

    def __init__(self, outcomes: list[tuple[WritebackStatus, bool, str | None]]) -> None:
        self.outcomes = outcomes
        self.store = FakeStore()
        self.calls = 0

    async def write_outcome(self, state: dict[str, Any]) -> WritebackOutcome:
        status, retryable, error = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        if status is WritebackStatus.ok:
            self.store.value = status
        return WritebackOutcome(
            "clinic", state["session_id"], status, 1, error=error, retryable=retryable
        )


def consumer_for(
    writeback: FakeWriteback, briefings: FakeBriefings | None = None
) -> Callable[[AsyncSession, BusEvent], Awaitable[None]]:
    return writeback_consumer(cast(Any, FakeRuntime()), cast(Any, writeback), briefings)


class FakeBriefings:
    """`BriefingService.ensure_document` with scripted outcomes (M2-12)."""

    def __init__(self, outcomes: list[DocumentOutcome]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    async def ensure_document(self, state: dict[str, Any]) -> DocumentOutcome:
        outcome = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        return outcome


@pytest.mark.parametrize(
    ("exc", "poison"),
    [
        (WritebackRejected("HTTP 422"), True),
        (PermanentFailure("never"), True),
        (WritebackUnavailable("ConnectError"), False),
        (TransientFailure("later"), False),
    ],
    ids=["rejected", "permanent", "unavailable", "transient"],
)
def test_only_a_permanent_handler_failure_is_poison(exc: Exception, poison: bool) -> None:
    assert is_poison(exc) is poison


async def test_a_writeback_during_an_ehr_outage_is_retried_until_it_succeeds(
    app: FastAPI,
    new_clinic: Callable[[], uuid.UUID],
    redis_client: Redis,
    stream: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The defect: the consumer logged the failed write and returned, so the event was
    acknowledged and never retried. Now it stays pending until the EHR answers."""
    caplog.set_level(logging.WARNING, logger="careflow_api.events")
    clinic = new_clinic()
    bus = fast_bus(redis_client, stream, max_deliveries=2)  # far more failures than this
    await bus.ensure_group(GROUP)
    await confirmed(redis_client, stream, clinic)
    outage = (WritebackStatus.failed, True, "ConnectError")
    writeback = FakeWriteback([outage, outage, outage, outage, (WritebackStatus.ok, False, None)])

    async def written() -> bool:
        if writeback.store.value is not WritebackStatus.ok:
            return False
        return await pending(redis_client, stream) == 0

    consumer = bus.subscribe(GROUP, consumer_for(writeback))
    await wait_for(written, "the write-back to succeed")
    await asyncio.sleep(0.3)  # several more claim rounds: it is not written again
    await stop(consumer)

    assert writeback.calls == 5
    assert await processed(clinic) == 1  # recorded once, by the delivery that succeeded
    assert not await redis_client.exists(bus.dead_stream)
    messages = [r.getMessage() for r in caplog.records if r.name == "careflow_api.events"]
    assert messages.count("events.handler_deferred") == 4
    assert "events.handler_failed" not in messages  # an outage is not a crash


async def test_a_rejected_writeback_is_dead_lettered_with_its_reason(
    app: FastAPI,
    new_clinic: Callable[[], uuid.UUID],
    redis_client: Redis,
    stream: str,
) -> None:
    clinic = new_clinic()
    bus = fast_bus(redis_client, stream, max_deliveries=3)
    await bus.ensure_group(GROUP)
    stream_id, session_id = await confirmed(redis_client, stream, clinic)
    writeback = FakeWriteback([(WritebackStatus.failed, False, "HTTP 422")])

    async def dead() -> bool:
        return (
            await redis_client.xlen(bus.dead_stream) == 1
            and await pending(redis_client, stream) == 0
        )

    consumer = bus.subscribe(GROUP, consumer_for(writeback))
    await wait_for(dead, "the dead letter")
    await asyncio.sleep(0.3)
    await stop(consumer)

    [(_, letter)] = await xrange(redis_client, bus.dead_stream)
    assert (letter["source_id"], letter["session_id"]) == (stream_id, session_id)
    assert (letter["deliveries"], letter["error"]) == ("3", "WritebackRejected: HTTP 422")
    assert writeback.calls == 3
    assert await processed(clinic) == 0  # never recorded as handled


async def test_a_successful_writeback_is_acknowledged_once(
    app: FastAPI,
    new_clinic: Callable[[], uuid.UUID],
    redis_client: Redis,
    stream: str,
) -> None:
    clinic = new_clinic()
    bus = fast_bus(redis_client, stream)
    await bus.ensure_group(GROUP)
    await confirmed(redis_client, stream, clinic)
    writeback = FakeWriteback([(WritebackStatus.ok, False, None)])

    async def done() -> bool:
        return writeback.calls == 1 and await pending(redis_client, stream) == 0

    consumer = bus.subscribe(GROUP, consumer_for(writeback))
    await wait_for(done, "the write-back")
    await asyncio.sleep(0.2)
    await stop(consumer)
    assert (writeback.calls, await processed(clinic)) == (1, 1)


async def test_a_briefing_document_the_ehr_did_not_take_is_written_with_the_triage_retries(
    app: FastAPI,
    new_clinic: Callable[[], uuid.UUID],
    redis_client: Redis,
    stream: str,
) -> None:
    """M2-12: the EHR was down when the nurse decided. The triage is written on a retry; the
    briefing's document, written once at the decision, used to stay unwritten. Now the event
    stays pending until the document is in the EHR too."""
    clinic = new_clinic()
    bus = fast_bus(redis_client, stream, max_deliveries=2)
    await bus.ensure_group(GROUP)
    await confirmed(redis_client, stream, clinic)
    writeback = FakeWriteback([(WritebackStatus.ok, False, None)])
    unreachable = DocumentOutcome(WritebackStatus.failed, error="ConnectError", retryable=True)
    briefings = FakeBriefings([unreachable, unreachable, DocumentOutcome(WritebackStatus.ok)])

    async def written() -> bool:
        return briefings.calls == 3 and await pending(redis_client, stream) == 0

    consumer = bus.subscribe(GROUP, consumer_for(writeback, briefings))
    await wait_for(written, "the briefing document to be written")
    await asyncio.sleep(0.3)  # more claim rounds: nothing is written again
    await stop(consumer)
    assert (writeback.calls, briefings.calls) == (1, 3)  # the triage once, the document retried
    assert await processed(clinic) == 1
    assert not await redis_client.exists(bus.dead_stream)


async def test_a_rejected_briefing_document_is_dead_lettered_with_its_reason(
    app: FastAPI,
    new_clinic: Callable[[], uuid.UUID],
    redis_client: Redis,
    stream: str,
) -> None:
    clinic = new_clinic()
    bus = fast_bus(redis_client, stream, max_deliveries=2)
    await bus.ensure_group(GROUP)
    await confirmed(redis_client, stream, clinic)
    writeback = FakeWriteback([(WritebackStatus.ok, False, None)])
    briefings = FakeBriefings([DocumentOutcome(WritebackStatus.failed, error="HTTP 422")])

    async def dead() -> bool:
        return (
            await redis_client.xlen(bus.dead_stream) == 1
            and await pending(redis_client, stream) == 0
        )

    consumer = bus.subscribe(GROUP, consumer_for(writeback, briefings))
    await wait_for(dead, "the dead letter")
    await stop(consumer)
    [(_, letter)] = await xrange(redis_client, bus.dead_stream)
    assert letter["error"] == "WritebackRejected: briefing document: HTTP 422"
    assert await processed(clinic) == 0


@pytest.mark.parametrize("command", ["xreadgroup", "xgroup_create", "xautoclaim"])
async def test_the_consumer_survives_a_redis_outage(
    app: FastAPI,
    new_clinic: Callable[[], uuid.UUID],
    redis_client: Redis,
    stream: str,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    """The defect: a transport error in the read or reclaim loop ended the consumer task, and
    nothing restarted it. Now it backs off, resumes, and handles what arrived meanwhile."""
    monkeypatch.setattr(bus_module, "RECONNECT_MIN_S", 0.01)
    clinic = new_clinic()
    bus = fast_bus(redis_client, stream)
    real = getattr(redis_client, command)
    failures: list[str] = []
    seen: list[str] = []

    async def down_three_times(*args: Any, **kwargs: Any) -> Any:
        if len(failures) < 3:
            failures.append(command)
            seen.append(bus.status.get(GROUP, ""))
            raise RedisConnectionError("Connection refused")
        return await real(*args, **kwargs)

    monkeypatch.setattr(redis_client, command, down_three_times)
    handled: list[str] = []

    async def handler(db: AsyncSession, event: BusEvent) -> None:
        handled.append(str(event.event_id))

    consumer = bus.subscribe(GROUP, handler)

    async def recovered() -> bool:
        return len(failures) == 3 and bus.status.get(GROUP) == "consuming"

    await wait_for(recovered, "the consumer to reconnect")
    await confirmed(redis_client, stream, clinic)

    async def done() -> bool:
        return len(handled) == 1 and await pending(redis_client, stream) == 0

    await wait_for(done, "the event sent after the outage")
    await stop(consumer)
    assert seen[1:] == ["reconnecting", "reconnecting"]
    assert await processed(clinic) == 1
