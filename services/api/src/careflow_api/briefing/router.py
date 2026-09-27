"""Doctor routes (/v1/doctor/*): the briefing (M1-16). The session list comes with M1-12."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from careflow_api.briefing.service import BriefingService
from careflow_api.deps import Principal, require_roles
from careflow_intake.briefing import BriefingNotReady
from careflow_intake.briefing.builder import briefing_json

router = APIRouter(prefix="/v1/doctor", tags=["doctor"])

NOT_FOUND = "No briefing for this session."


def briefing_service(request: Request) -> BriefingService:
    service: BriefingService = request.app.state.briefings
    return service


@router.get("/sessions/{session_id}/briefing", response_class=JSONResponse)
async def get_briefing(
    session_id: str,
    principal: Annotated[Principal, Depends(require_roles("doctor"))],
    service: Annotated[BriefingService, Depends(briefing_service)],
) -> JSONResponse:
    """The stored briefing, or one built now from the session's intake state (and stored)."""
    try:
        sid = uuid.UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=NOT_FOUND) from exc
    if (found := await service.store.get(principal.clinic_id, sid)) is not None:
        return JSONResponse(found)
    state = await service.states.load(str(principal.clinic_id), str(sid))
    if state is None or state.get("clinic_id") != str(principal.clinic_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    try:
        briefing = await service.make(state)
    except BriefingNotReady as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="The nurse has not confirmed this session yet."
        ) from exc
    return JSONResponse(briefing_json(briefing))


__all__ = ["router"]
