"""Admin routes (/v1/admin/*).

The status says what actually runs, not only what is configured (M1-17): a local model counts as
available only when its server answers, a missing urgency model or one trained on the synthetic
fixture is "degraded" and named as such, and replayed providers say "replay".
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select

from careflow_api.config import Settings, get_settings
from careflow_api.deps import Principal, require_roles
from careflow_api.models import FhirWritebackStatus
from careflow_api.rls import clinic_session
from careflow_api.routers.health import component_status
from careflow_contracts.api_models import AdminStatus, ProvidersResponse
from careflow_providers.adapters.replay import provider_mode

router = APIRouter(prefix="/v1/admin", tags=["admin"])
SERVICE_NAMES = {"db": "postgres", "redis": "valkey", "fhir": "hapi-fhir"}
LOCAL_PROBE_SECONDS = 1.0


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
    settings: Annotated[Settings, Depends(get_settings)],
    request: Request,
    principal: Annotated[Principal, Depends(require_roles("admin"))],
) -> AdminStatus:
    """The services, then every intake port's implementation (M1-12), the AI tasks' providers
    and models, and the versions of the ruleset and the urgency model."""
    components = await component_status(settings)
    services: list[dict[str, str]] = [
        {"name": SERVICE_NAMES[key], "status": value} for key, value in components.items()
    ]
    services.append(await writeback_row(request, principal.clinic_id))
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
        services.append(urgency_row(wiring))
    else:
        services.append(
            {"name": "intake", "status": "down", "detail": "intake runtime not started"}
        )
    services.extend(await speech_rows(settings))
    for task in await provider_rows():
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


async def writeback_row(request: Request, clinic_id: uuid.UUID) -> dict[str, str]:
    """The FHIR write-back as it runs: its bus consumer (ADR-043) and this clinic's write-backs
    that failed for good (an outage stays pending and is retried, so it is not counted)."""
    from careflow_api.intake.consumers import WRITEBACK_GROUP

    bus = getattr(request.app.state, "bus", None)
    consumer = bus.status.get(WRITEBACK_GROUP) if bus is not None else None
    async with clinic_session(clinic_id) as db:
        query = select(func.count()).select_from(FhirWritebackStatus)
        failed = (
            await db.execute(query.where(FhirWritebackStatus.status == "failed"))
        ).scalar_one()
    if consumer is None:
        status = "down"
    else:
        status = "ok" if consumer == "consuming" and failed == 0 else "degraded"
    detail = f"consumer {consumer or 'not running'}; {failed} failed write-backs in this clinic"
    return {"name": "fhir-writeback", "status": status, "detail": detail}


def voice_url(settings: Settings) -> str:
    return settings.voice_base_url or f"http://127.0.0.1:{os.environ.get('VOICE_PORT', '8090')}"


async def careflow_voice_answers(url: str) -> bool:
    """Whether CareFlow's voice service answers at `url`: another copy of the voice agent on the
    same port answers /healthz too, but without `service: careflow-voice`."""
    try:
        async with httpx.AsyncClient(timeout=LOCAL_PROBE_SECONDS) as client:
            response = await client.get(f"{url.rstrip('/')}/healthz")
        body = response.json() if response.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        return False
    return isinstance(body, dict) and body.get("service") == "careflow-voice"


async def speech_rows(settings: Settings) -> list[dict[str, str]]:
    """STT and TTS per language, from config/providers.yaml's speech section; "ok" only when the
    CareFlow voice service that runs them answers."""
    from careflow_intake.wiring import providers_config

    speech = providers_config().speech or {}
    url = voice_url(settings)
    up = await careflow_voice_answers(url)
    rows = []
    for kind in ("stt", "tts"):
        spec = speech.get(kind) or {}
        by_language = spec.get("by_language") or {}
        engines = ", ".join(f"{engine} ({lang})" for lang, engine in by_language.items())
        detail = engines or str(spec.get("provider", "not configured"))
        if not up:
            detail = f"{detail}; voice service not answering at {url}"
        rows.append(
            {"name": f"speech:{kind}", "status": "ok" if up else "down", "detail": detail[:200]}
        )
    return rows


def urgency_row(wiring: Any) -> dict[str, str]:
    """The urgency model as it really is: "degraded" when there is none (no suggestion, the
    nurse sets every level) or when it was trained on the synthetic fixture (not a result)."""
    version = str(wiring.urgency_model_version)
    if any(c.port == "urgency" and c.degraded for c in wiring.choices):
        return {"name": "urgency-model", "status": "degraded", "detail": "none: no suggestion"}
    if version.startswith("fixture-"):
        detail = f"{version}: trained on the synthetic fixture, not KTAS (NOT A RESULT)"
        return {"name": "urgency-model", "status": "degraded", "detail": detail[:200]}
    return {"name": "urgency-model", "status": "ok", "detail": version[:200]}


def cassette_present() -> bool:
    cassette = os.environ.get("CAREFLOW_CASSETTE", "")
    return bool(cassette) and Path(cassette).is_file()


async def local_server_answers(base_url: str) -> bool:
    """Whether the local model server (mlx_lm.server) answers `/models` right now."""
    try:
        async with httpx.AsyncClient(timeout=LOCAL_PROBE_SECONDS) as client:
            response = await client.get(f"{base_url.rstrip('/')}/models")
    except httpx.HTTPError:
        return False
    return response.status_code == 200


async def provider_rows() -> list[dict[str, Any]]:
    """Every AI task in config/providers.yaml: provider, model or tool, and whether it can run
    now. An API provider needs its key in the environment; a local one needs its server to
    answer (probed once per server); a CLI one runs offline jobs only."""
    from careflow_intake.wiring import providers_config

    rows: list[dict[str, Any]] = []
    answers: dict[str, bool] = {}
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
            base_url = str(body.get("base_url") or "")
            if base_url not in answers:
                answers[base_url] = bool(base_url) and await local_server_answers(base_url)
            row["available"] = answers[base_url]
            if not row["available"]:
                row["detail"] = f"model server not answering at {base_url}"[:200]
        mode = provider_mode()
        if mode == "replay":  # CAREFLOW_PROVIDER_MODE: no model is called at all
            row["provider"] = "replay"
            row["available"] = cassette_present()
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
    return ProvidersResponse.model_validate({"tasks": await provider_rows()})
