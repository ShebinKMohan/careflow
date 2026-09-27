"""The outbox relay (M1-11): publish unpublished outbox rows to the Redis Stream, every 100 ms.

For each clinic in turn, in a transaction RLS scopes to that clinic (so the relay needs no role
that sees every clinic): `SELECT ... WHERE published_at IS NULL ORDER BY seq LIMIT 100 FOR UPDATE
SKIP LOCKED`, `XADD careflow:events MAXLEN ~ 100000` each row, stamp `published_at`, commit.

- A crash between `XADD` and the commit publishes the row again later: consumers are idempotent.
- Several relays (API processes) may run: `SKIP LOCKED` gives each row to one of them.
- When Redis is down, rows stay unpublished (attempts and last error are counted) and go out once
  it is back; nothing is lost.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Final

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select

from careflow_api.db import get_sessionmaker
from careflow_api.events.streams import xadd
from careflow_api.models import Clinic, OutboxEvent
from careflow_api.rls import clinic_session

logger = logging.getLogger("careflow_api.events")

STREAM: Final = "careflow:events"
MAXLEN: Final = 100_000
BATCH: Final = 100
INTERVAL_SECONDS: Final = 0.1

type AfterPublish = Callable[[OutboxEvent], Awaitable[None]]


def utc_now() -> datetime:
    return datetime.now(UTC)


def stream_fields(row: OutboxEvent) -> dict[str, str]:
    return {
        "id": str(row.id),
        "type": row.event_type,
        "clinic_id": str(row.clinic_id),
        "session_id": row.session_id or "",
        "payload": json.dumps(row.payload, separators=(",", ":"), ensure_ascii=False),
        "created_at": row.created_at.isoformat(),
    }


class OutboxRelay:
    def __init__(
        self,
        redis: Redis,
        *,
        stream: str = STREAM,
        batch: int = BATCH,
        interval: float = INTERVAL_SECONDS,
        maxlen: int = MAXLEN,
        clock: Callable[[], datetime] = utc_now,
        after_publish: AfterPublish | None = None,
        clinic_ids: Sequence[uuid.UUID] | None = None,
    ) -> None:
        self.redis, self.stream, self.batch = redis, stream, batch
        self.interval, self.maxlen, self.clock = interval, maxlen, clock
        #: Test hook: runs after each XADD, before the commit (a crash there re-publishes).
        self.after_publish = after_publish
        #: Only these clinics (tests); None: every clinic.
        self.clinic_ids = None if clinic_ids is None else list(clinic_ids)

    async def clinics(self) -> list[uuid.UUID]:
        if self.clinic_ids is not None:
            return self.clinic_ids
        async with get_sessionmaker()() as db:
            return list((await db.execute(select(Clinic.id).order_by(Clinic.id))).scalars())

    async def publish_clinic(self, clinic_id: uuid.UUID) -> int:
        """Publish up to one batch of the clinic's unpublished rows, in order; the count sent."""
        query = (
            select(OutboxEvent)
            .where(OutboxEvent.published_at.is_(None))
            .order_by(OutboxEvent.seq)
            .limit(self.batch)
            .with_for_update(skip_locked=True)
        )
        sent = 0
        async with clinic_session(clinic_id) as db:
            for row in (await db.execute(query)).scalars().all():
                row.attempts += 1
                try:
                    await xadd(self.redis, self.stream, stream_fields(row), maxlen=self.maxlen)
                except RedisError as exc:  # keep the order: later rows wait for this one
                    row.last_error = type(exc).__name__
                    logger.warning("events.relay_failed", extra={"error": type(exc).__name__})
                    break
                if self.after_publish is not None:
                    await self.after_publish(row)
                row.published_at, row.last_error = self.clock(), None
                sent += 1
        return sent

    async def run_once(self) -> int:
        return sum([await self.publish_clinic(clinic_id) for clinic_id in await self.clinics()])

    async def run(self) -> None:
        """Forever: publish, then wait 100 ms (at once again after a full batch)."""
        while True:
            sent = 0
            try:
                sent = await self.run_once()
            except Exception:  # the next tick retries; the rows are still in the outbox
                logger.exception("events.relay_error")
            if sent < self.batch:
                await asyncio.sleep(self.interval)


__all__ = ["BATCH", "INTERVAL_SECONDS", "MAXLEN", "STREAM", "OutboxRelay", "stream_fields"]
