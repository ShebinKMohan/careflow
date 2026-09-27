"""`EventBus` (M1-11): consumer groups on the Redis Stream; at-least-once, idempotent handlers.

`subscribe(group, handler)` creates the group from `$` (ignoring BUSYGROUP) and runs a loop:
`XREADGROUP GROUP <group> <consumer> COUNT 50 BLOCK 1000 STREAMS careflow:events >`. For each
entry, one transaction scoped to the event's clinic first inserts `processed_events(group,
event_id)`; if that row already exists the handler is skipped (a re-delivery), otherwise the
handler runs. After the commit the entry is acknowledged (`XACK`). A handler that fails is not
acknowledged: `XAUTOCLAIM` (entries idle 30 s) retries it, at start and while running.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from redis.asyncio import Redis
from redis.exceptions import ResponseError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from careflow_api.events.relay import STREAM
from careflow_api.events.streams import Entry, entries, read_entries
from careflow_api.models import ProcessedEvent
from careflow_api.rls import clinic_session

logger = logging.getLogger("careflow_api.events")

COUNT: Final = 50
BLOCK_MS: Final = 1000
CLAIM_IDLE_MS: Final = 30_000


@dataclass(frozen=True)
class BusEvent:
    stream_id: str
    event_id: uuid.UUID
    type: str
    clinic_id: uuid.UUID
    session_id: str | None
    #: `{type, timestamp, data}`, as validated by the outbox.
    envelope: dict[str, Any]
    created_at: datetime

    @classmethod
    def parse(cls, stream_id: str, fields: dict[str, str]) -> BusEvent:
        return cls(
            stream_id=stream_id,
            event_id=uuid.UUID(fields["id"]),
            type=fields["type"],
            clinic_id=uuid.UUID(fields["clinic_id"]),
            session_id=fields.get("session_id") or None,
            envelope=json.loads(fields["payload"]),
            created_at=datetime.fromisoformat(fields["created_at"]),
        )


type Handler = Callable[[AsyncSession, BusEvent], Awaitable[None]]


def consumer_name() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


class EventBus:
    def __init__(
        self,
        redis: Redis,
        *,
        stream: str = STREAM,
        consumer: str | None = None,
        count: int = COUNT,
        block_ms: int = BLOCK_MS,
        claim_idle_ms: int = CLAIM_IDLE_MS,
    ) -> None:
        self.redis, self.stream, self.consumer = redis, stream, consumer or consumer_name()
        self.count, self.block_ms, self.claim_idle_ms = count, block_ms, claim_idle_ms

    def subscribe(self, group: str, handler: Handler) -> asyncio.Task[None]:
        """Start consuming for `group` in a background task (cancel it to stop)."""
        return asyncio.create_task(self.run(group, handler), name=f"events:{group}")

    async def ensure_group(self, group: str) -> None:
        try:
            await self.redis.xgroup_create(self.stream, group, id="$", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def handle(
        self, group: str, handler: Handler, stream_id: str, fields: dict[str, str]
    ) -> bool:
        """Run the handler once per event and group; acknowledge the entry. True if it ran."""
        event = BusEvent.parse(stream_id, fields)
        async with clinic_session(event.clinic_id) as db:
            statement = (
                insert(ProcessedEvent)
                .values(consumer_group=group, event_id=event.event_id, clinic_id=event.clinic_id)
                .on_conflict_do_nothing()
                .returning(ProcessedEvent.event_id)
            )
            first = (await db.execute(statement)).scalar_one_or_none() is not None
            if first:
                await handler(db, event)
        await self.redis.xack(self.stream, group, stream_id)
        return first

    async def _handle_all(self, group: str, handler: Handler, found: list[Entry]) -> None:
        for stream_id, fields in found:
            try:
                await self.handle(group, handler, stream_id, fields)
            except Exception:  # not acknowledged: XAUTOCLAIM retries it
                logger.exception("events.handler_failed", extra={"group": group})

    async def reclaim(self, group: str, handler: Handler) -> None:
        """Take over entries another consumer left pending for 30 s, and handle them."""
        cursor = "0-0"
        while True:
            reply = await self.redis.xautoclaim(
                self.stream,
                group,
                self.consumer,
                min_idle_time=self.claim_idle_ms,
                start_id=cursor,
                count=self.count,
            )
            cursor = str(reply[0])
            await self._handle_all(group, handler, entries(reply[1]))
            if cursor in ("0-0", "0"):
                return

    async def run(self, group: str, handler: Handler) -> None:
        await self.ensure_group(group)
        await self.reclaim(group, handler)
        claimed_at = time.monotonic()
        while True:
            reply = await self.redis.xreadgroup(
                group, self.consumer, {self.stream: ">"}, count=self.count, block=self.block_ms
            )
            await self._handle_all(group, handler, read_entries(reply))
            if time.monotonic() - claimed_at > self.claim_idle_ms / 1000:
                await self.reclaim(group, handler)
                claimed_at = time.monotonic()


__all__ = ["BLOCK_MS", "CLAIM_IDLE_MS", "COUNT", "BusEvent", "EventBus", "Handler"]
