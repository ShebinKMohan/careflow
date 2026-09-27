"""Queue routes (M1-10), as in `packages/contracts/openapi.yaml`.

Nurses call, mark arrived, mark missed and close tokens; a board device reads its department's
board (token code, room and status only); a patient session reads its own token.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from careflow_api.aftercommit import after_commit
from careflow_api.deps import Db, Principal, require_roles
from careflow_api.queue.core import InvalidTransition, RoomBusy
from careflow_api.queue.rooms import UnknownDepartment, UnknownRoom
from careflow_api.queue.service import QueueService, UnknownToken
from careflow_contracts.api_models import BoardEntry, CallNextResult, QueueToken

router = APIRouter(tags=["queue"])


def queue_service(request: Request) -> QueueService:
    service: QueueService = request.app.state.queue
    return service


Service = Annotated[QueueService, Depends(queue_service)]
Nurse = Annotated[Principal, Depends(require_roles("nurse"))]


def not_found(exc: LookupError) -> HTTPException:
    kind = {UnknownDepartment: "department", UnknownRoom: "room"}.get(type(exc), "token")
    return HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Unknown {kind}.")


def conflict(exc: ValueError) -> HTTPException:
    detail = "The room is busy." if isinstance(exc, RoomBusy) else "The token cannot do that now."
    return HTTPException(status.HTTP_409_CONFLICT, detail=detail)


@router.post("/v1/queue/{department}/call-next", response_model=CallNextResult)
async def call_next(
    department: str,
    principal: Nurse,
    db: Db,
    service: Service,
    room: str | None = None,
) -> CallNextResult:
    try:
        token = await service.call_next(
            db, clinic_id=principal.clinic_id, department=department, room=room
        )
    except (UnknownDepartment, UnknownRoom) as exc:
        raise not_found(exc) from exc
    except RoomBusy as exc:
        raise conflict(exc) from exc
    if token is not None:  # after the commit: every waiting phone moves up (M2-07)
        after_commit(db, lambda: service.broadcast(principal.clinic_id, department))
    return CallNextResult(token=token)


async def _change(
    action: str,
    token_id: str,
    principal: Principal,
    db: AsyncSession,
    service: QueueService,
) -> QueueToken:
    operations = {
        "arrived": service.mark_arrived,
        "no-show": service.mark_missed,
        "close": service.close,
    }
    try:
        token = await operations[action](db, clinic_id=principal.clinic_id, token_id=token_id)
    except UnknownToken as exc:
        raise not_found(exc) from exc
    except InvalidTransition as exc:
        raise conflict(exc) from exc
    after_commit(db, lambda: service.broadcast(principal.clinic_id, token.department))
    after_commit(db, lambda: service.tell(principal.clinic_id, token))  # its own phone, even closed
    return token


@router.post("/v1/queue/tokens/{token_id}/arrived", response_model=QueueToken)
async def mark_arrived(token_id: str, principal: Nurse, db: Db, service: Service) -> QueueToken:
    return await _change("arrived", token_id, principal, db, service)


@router.post("/v1/queue/tokens/{token_id}/no-show", response_model=QueueToken)
async def mark_no_show(token_id: str, principal: Nurse, db: Db, service: Service) -> QueueToken:
    return await _change("no-show", token_id, principal, db, service)


@router.post("/v1/queue/tokens/{token_id}/close", response_model=QueueToken)
async def close_token(token_id: str, principal: Nurse, db: Db, service: Service) -> QueueToken:
    return await _change("close", token_id, principal, db, service)


@router.get(
    "/v1/board/{department}",
    response_model=list[BoardEntry],
    dependencies=[Depends(require_roles("board"))],
)
async def board(department: str, db: Db, service: Service) -> list[BoardEntry]:
    try:
        return await service.board(db, department=department)
    except UnknownDepartment as exc:
        raise not_found(exc) from exc


@router.get("/v1/sessions/{session_id}/token", response_model=QueueToken)
async def session_token(
    session_id: str,
    principal: Annotated[Principal, Depends(require_roles("patient_session", "nurse"))],
    db: Db,
    service: Service,
) -> QueueToken:
    if principal.role == "patient_session" and principal.session_id != session_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Another session's token.")
    token = await service.session_token(db, session_id=session_id)
    if token is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No token for this session.")
    return token
