"""At-least-once delivery with exactly-once handling (M1-11), on Postgres and Valkey.

1,000 events go through the outbox. The first relay crashes three times between XADD and the
commit (so those rows are published twice) and is cancelled mid-run; a second relay finishes.
A consumer group must see every event and run its handler once per event.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from careflow_api.events.bus import BusEvent, EventBus
from careflow_api.events.outbox import OutboxPublisher
from careflow_api.events.publisher import Event
from careflow_api.events.relay import OutboxRelay
from careflow_api.events.streams import xrange
from careflow_api.models import OutboxEvent, ProcessedEvent
from careflow_api.rls import clinic_session
from careflow_contracts.events import TokenCalledData

NOW = datetime(2026, 9, 26, 9, 8, tzinfo=UTC)
TOTAL = 1000


async def wait_for(condition: Callable[[], Awaitable[bool]], limit_s: float, what: str) -> None:
    deadline = time.monotonic() + limit_s
    while not await condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.05)


async def unpublished(clinics: list[uuid.UUID]) -> int:
    total = 0
    for clinic in clinics:
        async with clinic_session(clinic) as db:
            query = (
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.published_at.is_(None))
            )
            total += (await db.execute(query)).scalar_one()
    return total


async def test_1000_events_survive_a_relay_crash_and_restart_and_run_once(
    app: FastAPI, new_clinic: Callable[[], uuid.UUID], redis_client: Redis, stream: str
) -> None:
    clinics = [new_clinic(), new_clinic()]
    publisher = OutboxPublisher()
    for batch in range(10):  # 10 transactions of 100 events, alternating clinics
        clinic = clinics[batch % 2]
        async with clinic_session(clinic) as db:
            for i in range(100):
                n = batch * 100 + i
                data = TokenCalledData(
                    clinic_id=str(clinic),
                    session_id=f"ses_{n}",
                    token_id=f"tok_{n}",
                    token_code=f"A-{n % 999 + 1:03d}",
                    department="emergency",
                    room="Resus 1",
                    called_at=NOW,
                )
                await publisher.add(db, Event("token.called", data, NOW))
    expected: set[str] = set()
    for clinic in clinics:
        async with clinic_session(clinic) as db:
            expected |= {str(i) for i in (await db.execute(select(OutboxEvent.id))).scalars()}
    assert len(expected) == TOTAL

    handled: list[uuid.UUID] = []

    async def side_effect(db: AsyncSession, event: BusEvent) -> None:
        handled.append(event.event_id)

    bus = EventBus(redis_client, stream=stream, block_ms=100)
    await bus.ensure_group("test-side-effect")
    consumer = bus.subscribe("test-side-effect", side_effect)

    crashes: list[int] = []

    async def crash_sometimes(row: OutboxEvent) -> None:
        if row.seq % 97 == 0 and len(crashes) < 3:
            crashes.append(row.seq)
            raise RuntimeError("relay crashed after XADD, before the commit")

    first = asyncio.create_task(
        OutboxRelay(
            redis_client,
            stream=stream,
            clinic_ids=clinics,
            interval=0.01,
            after_publish=crash_sometimes,
        ).run()
    )
    await wait_for(lambda: _published_at_least(clinics, 300), 30, "the first relay to publish 300")
    first.cancel()  # mid-run
    await asyncio.gather(first, return_exceptions=True)
    assert await unpublished(clinics) > 0, "the first relay must stop before the end"

    second = asyncio.create_task(
        OutboxRelay(redis_client, stream=stream, clinic_ids=clinics, interval=0.01).run()
    )
    await wait_for(lambda: _none_unpublished(clinics), 60, "the second relay to publish the rest")
    second.cancel()
    await asyncio.gather(second, return_exceptions=True)

    await wait_for(lambda: _handled(handled, TOTAL), 60, "the consumer to handle every event")
    await asyncio.sleep(0.5)  # any duplicate still in flight would show up now
    consumer.cancel()
    await asyncio.gather(consumer, return_exceptions=True)

    entries = await xrange(redis_client, stream)
    published_ids = [fields["id"] for _, fields in entries]
    print(
        f"{TOTAL} events: {len(crashes)} relay crashes after XADD, first relay cancelled mid-run; "
        f"{len(published_ids)} stream entries, {len(set(published_ids))} distinct, "
        f"{len(handled)} handled"
    )
    assert crashes and len(published_ids) > TOTAL  # the crashes published some rows twice
    assert set(published_ids) == expected  # every event at least once, zero lost
    assert (
        len(handled) == TOTAL and {str(i) for i in handled} == expected
    )  # side effect exactly once
    for clinic in clinics:
        async with clinic_session(clinic) as db:
            query = select(func.count()).select_from(ProcessedEvent)
            assert (await db.execute(query)).scalar_one() == TOTAL // 2
    assert json.loads(entries[0][1]["payload"])["type"] == "token.called"


async def _published_at_least(clinics: list[uuid.UUID], count: int) -> bool:
    return TOTAL - await unpublished(clinics) >= count


async def _none_unpublished(clinics: list[uuid.UUID]) -> bool:
    return await unpublished(clinics) == 0


async def _handled(handled: list[uuid.UUID], count: int) -> bool:
    return len(handled) >= count
