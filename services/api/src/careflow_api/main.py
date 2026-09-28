"""CareFlow API app factory: `uvicorn careflow_api.main:create_app --factory --port 8000`.

Research prototype. Synthetic data only. Not for clinical use.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry.sdk.trace import TracerProvider
from redis.asyncio import Redis

from careflow_api import problems, telemetry
from careflow_api.ask import AskService
from careflow_api.briefing import router as doctor
from careflow_api.briefing.service import BriefingService
from careflow_api.briefing.store import SqlBriefingStore
from careflow_api.config import Settings, get_settings
from careflow_api.events import tickets, ws
from careflow_api.events.bus import EventBus
from careflow_api.events.frames import QUEUE_STATUS, publish_frame
from careflow_api.events.outbox import OutboxPublisher
from careflow_api.events.relay import OutboxRelay
from careflow_api.events.ws import LiveHub
from careflow_api.hospital import fhir_endpoint
from careflow_api.intake.consumers import WRITEBACK_GROUP, writeback_consumer
from careflow_api.intake.runtime import IntakeRuntime
from careflow_api.intake.warmup import warm_local_extraction
from careflow_api.intake.wiring import build_ports
from careflow_api.queue import router as queue_router
from careflow_api.queue.rooms import rooms
from careflow_api.queue.service import Notify, QueueService, run_grace_expiry
from careflow_api.routers import (
    adaptive,
    admin,
    auth,
    companion,
    consent,
    dev,
    handover,
    health,
    identity,
    intake_summary,
    nurse,
    reception,
    sessions,
    start_checkin,
    urgent,
)
from careflow_api.writeback_store import SqlWritebackStore
from careflow_contracts.api_models import CompanionStatus
from careflow_intake.wiring import briefing_summarizer, providers_config
from careflow_integration.fhir.client import FhirClient
from careflow_providers.factory import build_provider
from careflow_rag.config import load_rag_config


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Background work of one API process, each switchable in settings: the queue's grace window
    every 10 s, the outbox relay every 100 ms and the live WebSocket fan-out."""
    settings = get_settings()
    tasks: list[asyncio.Task[None]] = []
    async with contextlib.AsyncExitStack() as stack:
        if settings.intake_runtime:
            wiring = build_ports(
                queue=app.state.queue, briefing=app.state.briefings, ask=app.state.ask
            )
            if settings.warm_extraction:
                app.state.extraction_warmup = await warm_local_extraction(wiring.ports.extractor)
            runtime = await stack.enter_async_context(
                IntakeRuntime.open(settings.database_url, wiring.ports)
            )
            app.state.wiring, app.state.intake = wiring, runtime
            app.state.briefings.states = runtime  # the briefing builds on demand from the graph
            if settings.writeback_consumer:
                handler = writeback_consumer(
                    runtime,
                    wiring.ports.writeback,  # type: ignore[arg-type]
                    briefings=app.state.briefings,
                )
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


def queue_notifier(app: FastAPI, settings: Settings) -> Notify:
    """`queue.status` frames to each waiting session's phone (M2-07), straight to the stream."""

    async def notify(clinic_id: uuid.UUID, statuses: list[tuple[str, CompanionStatus]]) -> None:
        now = datetime.now(UTC)
        for session_id, status in statuses:
            await publish_frame(
                app.state.redis,
                frame_type=QUEUE_STATUS,
                clinic_id=str(clinic_id),
                session_id=session_id,
                data=status.model_dump(mode="json"),
                now=now,
                stream=settings.event_stream,
            )

    return notify


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
    # The kiosk, staff and companion apps call the API from the browser; bearer tokens only, so
    # no cookies cross origins.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,
    )
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
    app.state.queue = QueueService(rooms(), app.state.events, notify=queue_notifier(app, settings))
    app.state.briefings = BriefingService(
        store=SqlBriefingStore(),
        writebacks=SqlWritebackStore(),
        fhir=lambda: FhirClient(fhir_endpoint()),
        summarizer=briefing_summarizer(),
    )
    # Ask the clinic (M2-09). config/rag.yaml is validated here; the `conversation` provider is
    # built on the first question, so a missing one refuses questions (fail safe) instead of
    # stopping the API.
    app.state.ask = AskService(
        load_rag_config(), lambda: build_provider("conversation", providers_config())
    )
    for module in (
        health,
        intake_summary,
        auth,
        sessions,
        consent,
        handover,
        start_checkin,
        reception,
        identity,
        adaptive,
        urgent,
        companion,
        nurse,
        doctor,
        admin,
        queue_router,
        tickets,
        ws,
    ):
        app.include_router(module.router)
    if settings.env == "dev":  # test hooks exist only in development (otherwise: 404)
        app.include_router(dev.router)
    telemetry.instrument(app, tracer_provider or telemetry.build_tracer_provider())
    return app
