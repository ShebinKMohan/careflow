"""CareFlow API app factory: `uvicorn careflow_api.main:create_app --factory --port 8000`.

Research prototype. Synthetic data only. Not for clinical use.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI
from opentelemetry.sdk.trace import TracerProvider
from redis.asyncio import Redis

from careflow_api import problems, telemetry
from careflow_api.briefing import router as doctor
from careflow_api.briefing.service import BriefingService
from careflow_api.briefing.store import SqlBriefingStore
from careflow_api.config import get_settings
from careflow_api.events import tickets, ws
from careflow_api.events.bus import EventBus
from careflow_api.events.outbox import OutboxPublisher
from careflow_api.events.relay import OutboxRelay
from careflow_api.events.ws import LiveHub
from careflow_api.intake.consumers import WRITEBACK_GROUP, writeback_consumer
from careflow_api.intake.runtime import IntakeRuntime
from careflow_api.intake.wiring import build_ports
from careflow_api.queue import router as queue_router
from careflow_api.queue.rooms import rooms
from careflow_api.queue.service import QueueService, run_grace_expiry
from careflow_api.routers import admin, auth, dev, health, nurse, sessions
from careflow_api.writeback_store import SqlWritebackStore
from careflow_intake.wiring import briefing_summarizer
from careflow_integration.fhir.client import FhirClient


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Background work of one API process, each switchable in settings: the queue's grace window
    every 10 s, the outbox relay every 100 ms and the live WebSocket fan-out."""
    settings = get_settings()
    tasks: list[asyncio.Task[None]] = []
    async with contextlib.AsyncExitStack() as stack:
        if settings.intake_runtime:
            wiring = build_ports(queue=app.state.queue, briefing=app.state.briefings)
            runtime = await stack.enter_async_context(
                IntakeRuntime.open(settings.database_url, wiring.ports)
            )
            app.state.wiring, app.state.intake = wiring, runtime
            app.state.briefings.states = runtime  # the briefing builds on demand from the graph
            if settings.writeback_consumer:
                handler = writeback_consumer(runtime, wiring.ports.writeback)  # type: ignore[arg-type]
                tasks.append(app.state.bus.subscribe(WRITEBACK_GROUP, handler))
        if settings.queue_grace_task:
            tasks.append(asyncio.create_task(run_grace_expiry(app.state.queue)))
        if settings.outbox_relay:
            tasks.append(asyncio.create_task(app.state.relay.run()))
        if settings.live_events:
            tasks.append(asyncio.create_task(app.state.hub.run()))
        yield
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await app.state.briefings.drain()
    await app.state.redis.aclose()


def create_app(*, tracer_provider: TracerProvider | None = None) -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="CareFlow API",
        version=settings.api_version,
        summary="Research prototype. Synthetic data only. Not for clinical use.",
        docs_url=None,  # packages/contracts/openapi.yaml is the contract, not FastAPI's schema
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    problems.install(app)
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.events = OutboxPublisher()
    app.state.relay = OutboxRelay(app.state.redis, stream=settings.event_stream)
    app.state.bus = EventBus(app.state.redis, stream=settings.event_stream)
    app.state.hub = LiveHub(
        app.state.redis,
        stream=settings.event_stream,
        heartbeat_seconds=settings.ws_heartbeat_seconds,
    )
    # config/rooms.yaml is validated here, so a bad file stops the API at start.
    app.state.queue = QueueService(rooms(), app.state.events)
    app.state.briefings = BriefingService(
        store=SqlBriefingStore(),
        writebacks=SqlWritebackStore(),
        fhir=lambda: FhirClient(settings.fhir_base_url),
        summarizer=briefing_summarizer(),
    )
    for module in (health, auth, sessions, nurse, doctor, admin, queue_router, tickets, ws):
        app.include_router(module.router)
    if settings.env == "dev":  # test hooks exist only in development (otherwise: 404)
        app.include_router(dev.router)
    telemetry.instrument(app, tracer_provider or telemetry.build_tracer_provider())
    return app
