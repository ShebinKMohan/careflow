"""The kiosk's consent screen (M2-05): record the patient's choices, and read the latest."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from careflow_api.consent import get_consent, record_consent
from careflow_api.deps import Principal, require_roles
from careflow_api.routers.sessions import own_session, session_row, utc_now
from careflow_contracts.api_models import ConsentRecord, ConsentRequest

router = APIRouter(tags=["consent"])


@router.post("/v1/sessions/{session_id}/consent", response_model=ConsentRecord)
async def post_consent(
    session_id: str,
    body: ConsentRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_roles("patient_session"))],
) -> ConsentRecord:
    """Append the patient's consent choices; the latest record wins."""
    own_session(principal, session_id)
    row = await session_row(principal, session_id)
    return await record_consent(
        principal.clinic_id,
        session_id,
        body,
        language=row.language,
        events=request.app.state.events,
        now=utc_now(),
    )


@router.get("/v1/sessions/{session_id}/consent", response_model=ConsentRecord)
async def read_consent(
    session_id: str,
    principal: Annotated[Principal, Depends(require_roles("patient_session"))],
) -> ConsentRecord:
    """The session's latest consent (the voice service's gate); 404 when there is none."""
    own_session(principal, session_id)
    await session_row(principal, session_id)
    record = await get_consent(principal.clinic_id, session_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No consent is recorded.")
    return record
