"""Bounded work during requests, rather than permanent serverless background workers."""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send

from careflow_api import aftercommit, nurse_flags
from careflow_api.events.streams import read_entries
from careflow_api.intake.consumers import WRITEBACK_GROUP
from careflow_api.rls import clinic_session

logger = logging.getLogger(__name__)


async def maintenance(app: FastAPI) -> None:
    """A single bounded pass; durable outbox/pending entries survive failures and cold starts."""
    # Every connection has its own send loop; live fanout is started only for an active socket.
    async with app.state.maintenance_lock:
        await aftercommit.drain()
        await app.state.briefings.drain()
        # Clinics are fixed synthetic installations seeded during deployment, not user tenants.
        for clinic in await app.state.relay.clinics():
            async with clinic_session(clinic) as db:
                changed = await app.state.queue.expire_grace(db, clinic_id=clinic)
                await nurse_flags.purge_stale(db, app.state.queue.clock())
            for department in {token.department for token in changed}:
                await app.state.queue.broadcast(clinic, department)
        await aftercommit.drain()
        # Create the consumer before publishing so its first pass cannot miss old outbox rows.
        bus = app.state.bus
        await bus.ensure_group(WRITEBACK_GROUP)
        await app.state.relay.run_once()
        await bus.reclaim(WRITEBACK_GROUP, app.state.writeback_handler)
        found = await bus.redis.xreadgroup(
            WRITEBACK_GROUP,
            bus.consumer,
            {bus.stream: ">"},
            count=bus.count,
        )
        bus.status[WRITEBACK_GROUP] = "consuming"
        await bus._handle_all(WRITEBACK_GROUP, app.state.writeback_handler, read_entries(found))
        await app.state.briefings.drain()
        await app.state.relay.run_once()


class RequestWork:
    """Run completion work before the response reaches Vercel's invocation boundary.

    Buffer HTTP output (CareFlow's JSON responses are small) until background tasks, queue
    notifications, FHIR document builds and an outbox pass finish. WebSockets stay streaming.
    The workflow result is never changed into a fabricated success when maintenance fails.
    """

    def __init__(self, app: ASGIApp, *, careflow: FastAPI) -> None:
        self.app, self.careflow = app, careflow

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        read_only = scope.get("method") == "GET" and not (
            path.endswith("/status") or path.startswith(("/v1/nurse/", "/v1/queue/"))
        )
        if scope["type"] != "http" or path.startswith("/v1/auth/") or read_only:
            await self.app(scope, receive, send)
            return
        messages = []

        async def collect(message):  # type: ignore[no-untyped-def]
            messages.append(message)

        await self.app(scope, receive, collect)
        try:
            async with asyncio.timeout(35):
                await maintenance(self.careflow)
        except Exception as exc:
            # The committed outbox remains available for the next request; log types only.
            logger.warning("serverless.maintenance_deferred", extra={"error": type(exc).__name__})
        for message in messages:
            await send(message)
