"""Admin routes (/v1/admin/*)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from careflow_api.config import Settings, get_settings
from careflow_api.deps import require_roles
from careflow_api.routers.health import component_status
from careflow_contracts.api_models import AdminStatus

router = APIRouter(prefix="/v1/admin", tags=["admin"])
SERVICE_NAMES = {"db": "postgres", "redis": "valkey", "fhir": "hapi-fhir"}


def ruleset_version() -> str:
    try:
        from careflow_rules import RULESET_VERSION  # type: ignore[attr-defined]  # M1-01
    except ImportError:
        return "unreleased"
    return str(RULESET_VERSION)


@router.get("/status", response_model=AdminStatus, dependencies=[Depends(require_roles("admin"))])
async def admin_status(settings: Annotated[Settings, Depends(get_settings)]) -> AdminStatus:
    components = await component_status(settings)
    return AdminStatus.model_validate(
        {
            "api_version": settings.api_version,
            "ruleset_version": ruleset_version(),
            "services": [
                {"name": SERVICE_NAMES[key], "status": value} for key, value in components.items()
            ],
        }
    )
