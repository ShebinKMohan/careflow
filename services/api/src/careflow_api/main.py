"""CareFlow API app factory: `uvicorn careflow_api.main:create_app --factory --port 8000`.

Research prototype. Synthetic data only. Not for clinical use.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI
from opentelemetry.sdk.trace import TracerProvider

from careflow_api import problems, telemetry
from careflow_api.config import get_settings
from careflow_api.events.publisher import MemoryEventPublisher
from careflow_api.queue import router as queue_router
from careflow_api.queue.rooms import rooms
from careflow_api.queue.service import QueueService, run_grace_expiry
from careflow_api.routers import admin, auth, dev, health, nurse


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Runs the queue's grace-window expiry every 10 s (off with CAREFLOW_QUEUE_GRACE_TASK=0)."""
    task = None
    if get_settings().queue_grace_task:
        task = asyncio.create_task(run_grace_expiry(app.state.queue))
    yield
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


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
    # config/rooms.yaml is validated here, so a bad file stops the API at start.
    app.state.events = MemoryEventPublisher()  # until M1-11's outbox
    app.state.queue = QueueService(rooms(), app.state.events)
    for module in (health, auth, nurse, admin, queue_router):
        app.include_router(module.router)
    if settings.env == "dev":  # test hooks exist only in development (otherwise: 404)
        app.include_router(dev.router)
    telemetry.instrument(app, tracer_provider or telemetry.build_tracer_provider())
    return app
