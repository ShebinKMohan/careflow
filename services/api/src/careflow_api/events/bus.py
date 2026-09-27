"""`EventBus` (M1-11): consumer groups on the Redis Stream; at-least-once, idempotent handlers.

`subscribe(group, handler)` creates the group from `$` (ignoring BUSYGROUP) and runs a loop:
`XREADGROUP GROUP <group> <consumer> COUNT 50 BLOCK 1000 STREAMS careflow:events >`. For each
entry, one transaction scoped to the event's clinic first inserts `processed_events(group,
event_id)`; if that row already exists the handler is skipped (a re-delivery), otherwise the
handler runs. After the commit the entry is acknowledged (`XACK`). A handler that fails is not
acknowledged: `XAUTOCLAIM` (entries idle 30 s) retries it, at start and while running.

A poison entry fails the same way on every delivery (ADR-043): it is not a CareFlow event, the
database rejects its data (`IntegrityError`, `DataError`), as when its clinic does not exist, or
its handler raised `PermanentFailure` (the EHR rejected the write). Once XPENDING counts 5
deliveries, it is copied to `careflow:events:dead` with the error and acknowledged. Every other
failure (Postgres or Redis down, a timeout, a `TransientFailure` such as an EHR outage) is
retried for as long as it lasts: a handler reports a failure by raising, never by returning.

A Redis outage never ends a consumer: `run` waits (0.5 s, doubling to 30 s) and resumes, and
`status` says which groups are consuming and which are reconnecting.
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
from redis.exceptions import RedisError, ResponseError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DataError, DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from careflow_api.events.relay import MAXLEN, STREAM, utc_now
from careflow_api.events.streams import Entry, entries, read_entries, xadd
from careflow_api.models import ProcessedEvent
from careflow_api.rls import clinic_session

logger = logging.getLogger("careflow_api.events")

COUNT: Final = 50
BLOCK_MS: Final = 1000
CLAIM_IDLE_MS: Final = 30_000
#: Deliveries of a poison entry before it is dead-lettered (1: at its first failure).
MAX_DELIVERIES: Final = 5
#: Poison entries go to `<stream>:dead`: `careflow:events:dead`.
DEAD_SUFFIX: Final = ":dead"
ERROR_CHARS: Final = 500
#: Waits between attempts to reach Redis again after it failed (seconds).
RECONNECT_MIN_S: Final = 0.5
RECONNECT_MAX_S: Final = 30.0


class MalformedEntry(ValueError):
    """A stream entry that is not a CareFlow event: a field is missing or unreadable."""


class PermanentFailure(Exception):  # noqa: N818 - a failure category, not an error type
    """Raised by a handler that can never process this entry (e.g. the EHR rejects the write):
    like a malformed entry, it is dead-lettered after `max_deliveries`, with its error."""


class TransientFailure(Exception):  # noqa: N818 - a failure category, not an error type
    """Raised by a handler that failed for now (e.g. the EHR is down): the entry stays pending
    and is retried until it succeeds. Logged without a traceback."""


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
        """Raise `MalformedEntry` for an entry that is not a CareFlow event."""
        try:
            return cls(
                stream_id=stream_id,
                event_id=uuid.UUID(fields["id"]),
                type=fields["type"],
                clinic_id=uuid.UUID(fields["clinic_id"]),
                session_id=fields.get("session_id") or None,
                envelope=json.loads(fields["payload"]),
                created_at=datetime.fromisoformat(fields["created_at"]),
            )
        except (KeyError, ValueError) as exc:
            raise MalformedEntry(f"{type(exc).__name__}: {exc}") from exc


type Handler = Callable[[AsyncSession, BusEvent], Awaitable[None]]


def consumer_name() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


def is_poison(exc: BaseException) -> bool:
    """True when the entry itself is at fault, so every delivery fails the same way: it is
    malformed, or the database rejects its data (an unknown clinic breaks the `processed_events`
    foreign key), or its handler says it never can. Anything else may pass on a later delivery."""
    return isinstance(exc, MalformedEntry | IntegrityError | DataError | PermanentFailure)


def describe(exc: BaseException) -> str:
    """`<type>: <message>` on one line; for a database error, the driver's message without the
    statement or its parameters."""
    detail = exc.orig if isinstance(exc, DBAPIError) and exc.orig is not None else exc
    return " ".join(f"{type(exc).__name__}: {detail}".split())[:ERROR_CHARS]


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
        max_deliveries: int = MAX_DELIVERIES,
    ) -> None:
        self.redis, self.stream, self.consumer = redis, stream, consumer or consumer_name()
        self.count, self.block_ms, self.claim_idle_ms = count, block_ms, claim_idle_ms
        self.max_deliveries, self.dead_stream = max_deliveries, f"{stream}{DEAD_SUFFIX}"
        #: Per group: "starting", "consuming" or "reconnecting" (Redis failed; `run` retries).
        self.status: dict[str, str] = {}

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

    async def deliveries(self, group: str, stream_id: str) -> int:
        """How many times the group has delivered the entry (XPENDING counts each XREADGROUP and
        XAUTOCLAIM); 0 once this consumer no longer holds it."""
        pending = await self.redis.xpending_range(
            self.stream, group, min=stream_id, max=stream_id, count=1, consumername=self.consumer
        )
        return int(pending[0]["times_delivered"]) if pending else 0

    async def dead_letter(
        self, group: str, stream_id: str, fields: dict[str, str], *, error: str, deliveries: int
    ) -> str:
        """Copy the entry to the dead-letter stream with its error, then acknowledge it. A crash in
        between leaves it pending, so it is dead-lettered again: twice there, never lost."""
        dead = {
            **fields,
            "source_stream": self.stream,
            "source_id": stream_id,
            "group": group,
            "consumer": self.consumer,
            "deliveries": str(deliveries),
            "error": error,
            "dead_at": utc_now().isoformat(),
        }
        dead_id = await xadd(self.redis, self.dead_stream, dead, maxlen=MAXLEN)
        await self.redis.xack(self.stream, group, stream_id)
        return dead_id

    async def _poisoned(
        self, group: str, stream_id: str, fields: dict[str, str], exc: Exception
    ) -> None:
        """Dead-letter a poison entry at its `max_deliveries`-th delivery; until then it stays
        pending. If Redis fails here, it stays pending too, and a later delivery tries again."""
        try:
            deliveries = await self.deliveries(group, stream_id)
            if deliveries < self.max_deliveries:
                return
            await self.dead_letter(
                group, stream_id, fields, error=describe(exc), deliveries=deliveries
            )
        except RedisError:
            logger.exception("events.dead_letter_failed", extra={"group": group})
            return
        logger.error(
            "events.dead_lettered",
            extra={
                "group": group,
                "stream_id": stream_id,
                "type": fields.get("type"),
                "deliveries": deliveries,
                "error": type(exc).__name__,
            },
        )

    async def _handle_all(self, group: str, handler: Handler, found: list[Entry]) -> None:
        for stream_id, fields in found:
            try:
                await self.handle(group, handler, stream_id, fields)
            except TransientFailure as exc:  # not acknowledged: XAUTOCLAIM retries it
                logger.warning(
                    "events.handler_deferred",
                    extra={"group": group, "stream_id": stream_id, "error": describe(exc)},
                )
            except Exception as exc:  # not acknowledged: XAUTOCLAIM retries it, unless poison
                logger.exception("events.handler_failed", extra={"group": group})
                if is_poison(exc):
                    await self._poisoned(group, stream_id, fields, exc)

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
        """Consume `group` until cancelled. A Redis failure (the connection drops, Valkey
        restarts) never ends it: it waits, doubling from 0.5 s to 30 s, and starts over from the
        group (re-created if Valkey lost it) and the entries left pending."""
        delay = RECONNECT_MIN_S
        self.status[group] = "starting"
        while True:
            try:
                await self._consume(group, handler)
            except RedisError as exc:
                if self.status[group] == "consuming":
                    delay = RECONNECT_MIN_S  # it had recovered: start the back-off over
                self.status[group] = "reconnecting"
                logger.warning(
                    "events.redis_unavailable",
                    extra={"group": group, "error": describe(exc), "retry_in_s": delay},
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX_S)

    async def _consume(self, group: str, handler: Handler) -> None:
        await self.ensure_group(group)
        await self.reclaim(group, handler)
        claimed_at = time.monotonic()
        while True:
            reply = await self.redis.xreadgroup(
                group, self.consumer, {self.stream: ">"}, count=self.count, block=self.block_ms
            )
            self.status[group] = "consuming"
            await self._handle_all(group, handler, read_entries(reply))
            if time.monotonic() - claimed_at > self.claim_idle_ms / 1000:
                await self.reclaim(group, handler)
                claimed_at = time.monotonic()


__all__ = [
    "BLOCK_MS",
    "CLAIM_IDLE_MS",
    "COUNT",
    "DEAD_SUFFIX",
    "MAX_DELIVERIES",
    "RECONNECT_MAX_S",
    "RECONNECT_MIN_S",
    "BusEvent",
    "EventBus",
    "Handler",
    "MalformedEntry",
    "PermanentFailure",
    "TransientFailure",
    "describe",
    "is_poison",
]
