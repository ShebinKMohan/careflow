"""Doctor routes (/v1/doctor/*): the confirmed sessions (M1-12) and the briefing (M1-16)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from careflow_api.briefing.service import BriefingService
from careflow_api.deps import Principal, require_roles
from careflow_api.models import PatientSession
from careflow_api.rls import clinic_session
from careflow_contracts.api_models import AlertChip, DoctorSessionSummary
from careflow_intake.briefing import BriefingNotReady
from careflow_intake.briefing.builder import briefing_json

router = APIRouter(prefix="/v1/doctor", tags=["doctor"])

NOT_FOUND = "No briefing for this session."


def briefing_service(request: Request) -> BriefingService:
    service: BriefingService = request.app.state.briefings
    return service


@router.get("/sessions", response_model=list[DoctorSessionSummary])
async def doctor_sessions(
    principal: Annotated[Principal, Depends(require_roles("doctor"))],
    request: Request,
) -> list[DoctorSessionSummary]:
    """Today's sessions a nurse has confirmed, with their token and alerts (M1-12)."""
    intake = getattr(request.app.state, "intake", None)
    if intake is None:
        return []
    since = datetime.now(UTC) - timedelta(hours=24)
    query = (
        select(PatientSession)
        .where(PatientSession.started_at >= since)
        .order_by(PatientSession.started_at)
    )
    async with clinic_session(principal.clinic_id) as db:
        rows = list((await db.execute(query)).scalars())
    found: list[DoctorSessionSummary] = []
    for row in rows:
        state = await intake.state(str(row.id))
        decision = state.get("nurse_decision") if state else None
        if state is None or decision is None:
            continue
        async with clinic_session(principal.clinic_id) as db:
            token = await request.app.state.queue.session_token(db, session_id=str(row.id))
        if token is None:
            continue
        found.append(
            DoctorSessionSummary(
                session_id=str(row.id),
                token_code=token.token_code,
                arrived_at=state["arrived_at"],
                confirmed_level=decision.final_level,
                scale=decision.scale,
                alert_chips=[
                    AlertChip(
                        alert_id=a.alert_id,
                        rule_id=a.rule_id,
                        severity=a.severity,
                        partial=a.partial,
                    )
                    for a in state.get("alerts", [])
                ],
            )
        )
    return found


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
