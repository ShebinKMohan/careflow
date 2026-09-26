"""Nurse routes (/v1/nurse/*). The worklist is empty until intake exists (M1-12)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from careflow_api.deps import require_roles
from careflow_contracts.api_models import WorklistItem

router = APIRouter(prefix="/v1/nurse", tags=["nurse"])


@router.get(
    "/worklist",
    response_model=list[WorklistItem],
    dependencies=[Depends(require_roles("nurse", "admin"))],
)
async def worklist() -> list[WorklistItem]:
    return []
