"""Live events over WebSockets (M1-11).

Every API process runs one `LiveHub`: a plain `XREAD BLOCK` loop on the stream from `$`. It uses
no consumer group, which would split events between processes. The hub pushes each event to the
connections that may see it.

- `/v1/ws/staff?ticket=`: events of the ticket's clinic, by role. Nurse: alerts, triage, tokens,
  intake, and patient and nurse flags. Doctor: `triage.confirmed` and `intake.completed`. Board:
  `token.called` and `token.no_show`. Admin: every webhook event type.
- `/v1/ws/sessions/{session_id}?ticket=`: that session's patient-safe events only
  (`token.called`, `token.no_show`, `intake.completed`). Never an alert, a suggestion or a level.

Frames are `{"id": <stream id>, "type", "timestamp", "data"}`, plus `{"type": "heartbeat"}` after
20 s of silence. `?last_event_id=<stream id>` replays what a reconnecting client missed, up to
5 minutes back. Internal (bus-only) events never reach a socket. A missing, used or expired
ticket closes with 4401; the wrong channel for the role closes with 4403.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any, Final

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from redis.asyncio import Redis

from careflow_api.events.relay import STREAM
from careflow_api.events.streams import read_entries, xrange
from careflow_api.events.tickets import SESSION_ROLES, STAFF_ROLES, TicketClaims, redeem
from careflow_contracts.events import EventType

logger = logging.getLogger("careflow_api.events")

WEBHOOK_TYPES: Final = frozenset(e.value for e in EventType)
NURSE_FAMILIES: Final = frozenset({"alert", "triage", "token", "intake", "patient", "nurse"})
STAFF_TYPES: Final[dict[str, frozenset[str]]] = {
    "nurse": frozenset(t for t in WEBHOOK_TYPES if t.split(".", 1)[0] in NURSE_FAMILIES)
    | {"patient.help_requested"},
    "doctor": frozenset({"triage.confirmed", "intake.completed"}),
    "board": frozenset({"token.called", "token.no_show"}),
    "admin": WEBHOOK_TYPES | {"patient.help_requested"},
}
#: What a patient's own screen may receive: status, token and room. Never alerts or levels.
#: `session.handed_over` (M2-06) is a WebSocket frame only, never a webhook event.
SESSION_TYPES: Final = frozenset(
    {"token.called", "token.no_show", "intake.completed", "session.handed_over", "queue.status"}
)
REPLAY_SECONDS: Final = 300
QUEUE_SIZE: Final = 1000
CLOSE_TICKET: Final = 4401
CLOSE_FORBIDDEN: Final = 4403

router = APIRouter(tags=["realtime"])


def stream_key(stream_id: str) -> tuple[int, int]:
    milliseconds, _, sequence = stream_id.partition("-")
    return int(milliseconds), int(sequence or 0)


@dataclass(eq=False)
class Connection:
    websocket: WebSocket
    claims: TicketClaims
    queue: asyncio.Queue[tuple[str, dict[str, str]]] = field(
        default_factory=lambda: asyncio.Queue(QUEUE_SIZE)
    )
    #: Frames up to this stream id were already sent (by the replay).
    sent_up_to: tuple[int, int] = (0, 0)

    def wants(self, fields: dict[str, str]) -> bool:
        if fields.get("clinic_id") != self.claims.clinic_id:
            return False
        event_type = fields.get("type", "")
        if self.claims.channel == "staff":
            return event_type in STAFF_TYPES.get(self.claims.role, frozenset())
        return event_type in SESSION_TYPES and fields.get("session_id") == self.claims.session_id


def frame(stream_id: str, fields: dict[str, str]) -> dict[str, Any]:
    envelope: dict[str, Any] = json.loads(fields["payload"])
    return {"id": stream_id, **envelope}


class LiveHub:
    def __init__(
        self,
        redis: Redis,
        *,
        stream: str = STREAM,
        block_ms: int = 1000,
        heartbeat_seconds: float = 20.0,
    ) -> None:
        self.redis, self.stream, self.block_ms = redis, stream, block_ms
        self.heartbeat_seconds = heartbeat_seconds
        self.connections: set[Connection] = set()
        self._request_reader: asyncio.Task[None] | None = None

    async def run(self) -> None:
        """Read new stream entries forever and hand each to the connections that want it."""
        last = "$"
        while True:
            try:
                reply = await self.redis.xread({self.stream: last}, count=100, block=self.block_ms)
            except Exception:  # Redis away: the outbox keeps every event; retry shortly
                logger.exception("events.live_read_failed")
                await asyncio.sleep(1)
                continue
            for stream_id, fields in read_entries(reply):
                last = stream_id
                self.dispatch(stream_id, fields)

    def dispatch(self, stream_id: str, fields: dict[str, str]) -> None:
        for connection in list(self.connections):
            if connection.wants(fields):
                try:
                    connection.queue.put_nowait((stream_id, fields))
                except asyncio.QueueFull:  # a client this far behind reconnects and replays
                    logger.warning("events.live_queue_full")

    async def replay(self, connection: Connection, last_event_id: str) -> None:
        """Send what the client missed after `last_event_id`, at most 5 minutes back."""
        floor = (int(time.time() * 1000) - REPLAY_SECONDS * 1000, 0)
        start = max(stream_key(last_event_id), floor)
        found = await xrange(
            self.redis, self.stream, start=f"({start[0]}-{start[1]}", count=QUEUE_SIZE
        )
        for stream_id, fields in found:
            if connection.wants(fields):
                await self.send(connection, stream_id, fields)
            connection.sent_up_to = stream_key(stream_id)

    async def send(self, connection: Connection, stream_id: str, fields: dict[str, str]) -> None:
        await connection.websocket.send_json(frame(stream_id, fields))
        created = datetime.fromisoformat(fields["created_at"])
        delivery_ms = (datetime.now(UTC) - created).total_seconds() * 1000
        logger.info(
            "events.delivered",
            extra={"event_type": fields["type"], "event_delivery_ms": round(delivery_ms, 1)},
        )

    async def _send_loop(self, connection: Connection) -> None:
        while True:
            try:
                stream_id, fields = await asyncio.wait_for(
                    connection.queue.get(), timeout=self.heartbeat_seconds
                )
            except TimeoutError:
                now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                await connection.websocket.send_json({"type": "heartbeat", "timestamp": now})
                continue
            if stream_key(stream_id) > connection.sent_up_to:
                await self.send(connection, stream_id, fields)
                connection.sent_up_to = stream_key(stream_id)

    async def serve(
        self, websocket: WebSocket, claims: TicketClaims, last_event_id: str | None
    ) -> None:
        from careflow_api.config import get_settings

        # A Vercel invocation owns only its current sockets. Start the stream reader for this
        # invocation; stop it at disconnect. No always-on worker or instance affinity needed.
        connection = Connection(websocket, claims)
        self.connections.add(connection)  # before the replay, so nothing falls between them
        if get_settings().serverless and (
            self._request_reader is None or self._request_reader.done()
        ):
            self._request_reader = asyncio.create_task(self.run())
        try:
            if last_event_id:
                with contextlib.suppress(ValueError):
                    await self.replay(connection, last_event_id)
            sender = asyncio.create_task(self._send_loop(connection))
            receiver = asyncio.create_task(_until_disconnect(websocket))
            done, pending = await asyncio.wait(
                {sender, receiver}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                if not task.cancelled() and task.exception() is not None:
                    if not isinstance(task.exception(), WebSocketDisconnect | RuntimeError):
                        logger.warning("events.socket_closed", exc_info=task.exception())
        finally:
            self.connections.discard(connection)
            if not self.connections and self._request_reader is not None:
                self._request_reader.cancel()
                await asyncio.gather(self._request_reader, return_exceptions=True)
                self._request_reader = None


async def _until_disconnect(websocket: WebSocket) -> None:
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return


def hub_of(websocket: WebSocket) -> LiveHub:
    hub: LiveHub = websocket.app.state.hub
    return hub


async def _open(websocket: WebSocket, ticket: str | None) -> TicketClaims | None:
    await websocket.accept()
    claims = await redeem(hub_of(websocket).redis, ticket)
    if claims is None:
        await websocket.close(code=CLOSE_TICKET)
    return claims


@router.websocket("/v1/ws/staff")
async def staff_socket(
    websocket: WebSocket,
    ticket: Annotated[str | None, Query()] = None,
    last_event_id: Annotated[str | None, Query()] = None,
) -> None:
    claims = await _open(websocket, ticket)
    if claims is None:
        return
    if claims.channel != "staff" or claims.role not in STAFF_ROLES:
        await websocket.close(code=CLOSE_FORBIDDEN)
        return
    await hub_of(websocket).serve(websocket, claims, last_event_id)


@router.websocket("/v1/ws/sessions/{session_id}")
async def session_socket(
    websocket: WebSocket,
    session_id: str,
    ticket: Annotated[str | None, Query()] = None,
    last_event_id: Annotated[str | None, Query()] = None,
) -> None:
    claims = await _open(websocket, ticket)
    if claims is None:
        return
    if (
        claims.channel != "session"
        or claims.role not in SESSION_ROLES
        or claims.session_id != session_id
    ):
        await websocket.close(code=CLOSE_FORBIDDEN)
        return
    await hub_of(websocket).serve(websocket, claims, last_event_id)


__all__ = [
    "CLOSE_FORBIDDEN",
    "CLOSE_TICKET",
    "REPLAY_SECONDS",
    "SESSION_TYPES",
    "STAFF_TYPES",
    "LiveHub",
    "router",
]
