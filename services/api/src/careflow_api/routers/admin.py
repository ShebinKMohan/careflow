"""Admin routes (/v1/admin/*)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from careflow_api.config import Settings, get_settings
from careflow_api.deps import require_roles
from careflow_api.routers.health import component_status
from careflow_contracts.api_models import AdminStatus, ProvidersResponse
from careflow_providers.adapters.replay import provider_mode

router = APIRouter(prefix="/v1/admin", tags=["admin"])
SERVICE_NAMES = {"db": "postgres", "redis": "valkey", "fhir": "hapi-fhir"}


def ruleset_version() -> str:
    try:
        from careflow_rules import RULESET_VERSION  # type: ignore[attr-defined]  # M1-01
    except ImportError:
        return "unreleased"
    return str(RULESET_VERSION)


@router.get(
    "/status",
    response_model=AdminStatus,
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles("admin"))],
)
async def admin_status(
    settings: Annotated[Settings, Depends(get_settings)], request: Request
) -> AdminStatus:
    """The services, then every intake port's implementation (M1-12), the AI tasks' providers
    and models, and the versions of the ruleset and the urgency model."""
    components = await component_status(settings)
    services: list[dict[str, str]] = [
        {"name": SERVICE_NAMES[key], "status": value} for key, value in components.items()
    ]
    wiring = getattr(request.app.state, "wiring", None)
    if wiring is not None:
        for choice in wiring.choices:
            detail = (
                f"{choice.implementation}; {choice.detail}"
                if choice.detail
                else choice.implementation
            )
            services.append(
                {
                    "name": f"port:{choice.port}",
                    "status": "degraded" if choice.degraded else "ok",
                    "detail": detail[:200],
                }
            )
        no_model = any(c.port == "urgency" and c.degraded for c in wiring.choices)
        services.append(
            {
                "name": "urgency-model",
                "status": "degraded" if no_model else "ok",  # no suggestion: the nurse sets it
                "detail": wiring.urgency_model_version[:200],
            }
        )
    else:
        services.append(
            {"name": "intake", "status": "down", "detail": "intake runtime not started"}
        )
    for task in provider_rows():
        uses = task.get("model") or task.get("tool") or ""
        services.append(
            {
                "name": f"ai:{task['task']}",
                "status": "ok" if task["available"] else "degraded",
                "detail": f"{task['provider']} {uses}".strip(),
            }
        )
    return AdminStatus.model_validate(
        {
            "api_version": settings.api_version,
            "ruleset_version": ruleset_version(),
            "services": services,
        }
    )


def provider_rows() -> list[dict[str, Any]]:
    """Every AI task in config/providers.yaml: provider, model or tool, and whether it can run.
    An API provider needs its key in the environment; a CLI one runs offline jobs only."""
    from careflow_intake.wiring import providers_config

    rows: list[dict[str, Any]] = []
    for task, spec in providers_config().tasks.items():
        body = spec.model_dump()
        kind = str(body.get("provider"))
        row: dict[str, Any] = {"task": task, "provider": kind}
        if body.get("model"):
            row["model"] = str(body["model"])
        if body.get("tool"):
            row["tool"] = str(body["tool"])
        if kind == "api":
            vendor = str(body.get("vendor", "")).upper()
            row["available"] = bool(os.environ.get(f"{vendor}_API_KEY"))
            if not row["available"]:
                row["detail"] = "no API key configured"
        elif kind == "cli":
            row["available"] = shutil.which(str(body.get("tool", ""))) is not None
            row["detail"] = "offline jobs only"
        else:
            row["available"] = True
        mode = provider_mode()
        if mode == "replay":  # CAREFLOW_PROVIDER_MODE: no model is called at all
            cassette = os.environ.get("CAREFLOW_CASSETTE", "")
            row["provider"] = "replay"
            row["available"] = bool(cassette) and Path(cassette).is_file()
            row["detail"] = "recorded responses only; no model is called"
        elif mode == "record":
            row["detail"] = "recording every response to a cassette"
        rows.append(row)
    return rows


@router.get(
    "/providers",
    response_model=ProvidersResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles("admin"))],
)
async def admin_providers() -> ProvidersResponse:
    return ProvidersResponse.model_validate({"tasks": provider_rows()})
