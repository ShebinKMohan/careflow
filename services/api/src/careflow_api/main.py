"""CareFlow API app factory: `uvicorn careflow_api.main:create_app --factory --port 8000`.

Research prototype. Synthetic data only. Not for clinical use.
"""

from __future__ import annotations

from fastapi import FastAPI
from opentelemetry.sdk.trace import TracerProvider

from careflow_api import problems, telemetry
from careflow_api.config import get_settings
from careflow_api.routers import admin, auth, dev, health, nurse


def create_app(*, tracer_provider: TracerProvider | None = None) -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="CareFlow API",
        version=settings.api_version,
        summary="Research prototype. Synthetic data only. Not for clinical use.",
        docs_url=None,  # packages/contracts/openapi.yaml is the contract, not FastAPI's schema
        redoc_url=None,
        openapi_url=None,
    )
    problems.install(app)
    for module in (health, auth, nurse, admin):
        app.include_router(module.router)
    if settings.env == "dev":  # test hooks exist only in development (otherwise: 404)
        app.include_router(dev.router)
    telemetry.instrument(app, tracer_provider or telemetry.build_tracer_provider())
    return app
