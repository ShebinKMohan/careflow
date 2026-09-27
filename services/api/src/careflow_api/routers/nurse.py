"""Nurse routes (/v1/nurse/*, M1-12): the worklist, the decision, nurse vitals and tasks.

Only the decision route finalises a level (safety rule 3): it resumes the graph's `nurse_review`
pause. A decision while the session is not waiting there is 409; an invalid one (an override
without a reason, a level that differs from the suggestion without one) is 422 and leaves the
graph waiting. Every decision writes one `nurse_decisions` row.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from careflow_api.deps import Principal, require_roles
from careflow_api.feels_worse import summaries as feels_worse_summaries
from careflow_api.hospital import hospital_adapter
from careflow_api.intake.runtime import IntakeRuntime
from careflow_api.intake.views import ECG_ACTION, tasks, triage_view, worklist_item
from careflow_api.intake.views import task_id as ecg_task_id
from careflow_api.models import NurseDecisionRow, PatientSession
from careflow_api.nurse_flags import medical_questions
from careflow_api.rls import clinic_session
from careflow_api.routers.sessions import runtime, session_row, touch
from careflow_api.writeback_store import SqlWritebackStore
from careflow_contracts.api_models import (
    DecisionResult,
    NurseVitalsRequest,
    Task,
    TriageView,
    WorklistItem,
)
from careflow_contracts.generated.nurse_decision_schema import NurseDecision
from careflow_contracts.generated.structured_facts_schema import FactKey
from careflow_contracts.ids import stable_fact_id
from careflow_contracts.validators import iter_facts
from careflow_intake.facts import FactEntry
from careflow_intake.graph import NotWaiting
from careflow_intake.nodes.triage import InvalidNurseDecision
from careflow_intake.state import IntakeState, structured_facts
from careflow_integration.fhir.writeback.handler import UnknownTask, WritebackHandler

logger = logging.getLogger("careflow_api.nurse")

router = APIRouter(prefix="/v1/nurse", tags=["nurse"])

ACTIVE_WINDOW: Final = dt.timedelta(hours=24)
ACVPU_DISPLAY: Final = {
    "A": "Alert",
    "C": "New confusion",
    "V": "Voice",
    "P": "Pain",
    "U": "Unresponsive",
}
ACVPU_SYSTEM: Final = "https://careflow.example/fhir/CodeSystem/acvpu"
NURSE_UNITS: Final = {"respiratory_rate": "/min", "systolic_bp": "mm[Hg]"}


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def active_sessions(principal: Principal) -> list[PatientSession]:
    since = utc_now() - ACTIVE_WINDOW
    query = (
        select(PatientSession)
        .where(PatientSession.started_at >= since)
        .where(PatientSession.status.not_in(("closed", "no_show")))
        .order_by(PatientSession.started_at)
    )
    async with clinic_session(principal.clinic_id) as db:
        return list((await db.execute(query)).scalars())


@router.get(
    "/worklist",
    response_model=list[WorklistItem],
    response_model_exclude_unset=True,
)
async def worklist(
    principal: Annotated[Principal, Depends(require_roles("nurse", "admin"))],
    request: Request,
) -> list[WorklistItem]:
    """Today's open sessions with their alerts, suggestion, level, tasks and warnings, and the
    patient's "I feel worse" presses and refused medical questions. Empty while the intake runtime
    is not running (no intake can be in progress without it)."""
    intake: IntakeRuntime | None = getattr(request.app.state, "intake", None)
    if intake is None:
        logger.warning("nurse.worklist", extra={"error": "intake runtime not started"})
        return []
    items: list[WorklistItem] = []
    rows = await active_sessions(principal)
    presses = await feels_worse_summaries(principal.clinic_id, [str(row.id) for row in rows])
    questions = await medical_questions(principal.clinic_id, [str(row.id) for row in rows])
    for row in rows:
        session_id = str(row.id)
        state = await intake.state(session_id)
        if state is None:
            continue
        waiting, _ = await intake.waiting_for(session_id)
        async with clinic_session(principal.clinic_id) as db:
            token = await request.app.state.queue.session_token(db, session_id=session_id)
        items.append(
            worklist_item(
                state,
                patient_display=row.patient_display or "Unknown",
                waiting=waiting,
                token_code=token.token_code if token else None,
                token_status=token.status.value if token else None,
                feels_worse=presses.get(session_id),
                medical_questions=questions.get(session_id),
            )
        )
    return items


def snapshot(state: IntakeState) -> dict[str, Any]:
    """Coded values only (numbers, yes/no, codes), never free text: M5-02 and M5-03 read them."""
    coded: dict[str, Any] = {}
    for path, fact in iter_facts(structured_facts(state)):
        value: Any = getattr(fact, "value", None)
        if isinstance(value, bool | int | float):
            coded[path] = value
        elif hasattr(value, "code"):
            coded[path] = str(getattr(value.code, "value", value.code))
        elif hasattr(value, "value") and str(value.value) in ("yes", "no", "unknown"):
            coded[path] = str(value.value)
    return coded


@router.post(
    "/sessions/{session_id}/decision", response_model=DecisionResult, response_class=JSONResponse
)
async def decide(
    session_id: str,
    decision: NurseDecision,
    principal: Annotated[Principal, Depends(require_roles("nurse"))],
    intake: Annotated[IntakeRuntime, Depends(runtime)],
    request: Request,
) -> JSONResponse:
    """Confirm or override the level; the graph then queues the patient, writes the triage back
    to the EHR and builds the doctor briefing."""
    await session_row(principal, session_id)
    if decision.session_id != session_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="The decision is for another session."
        )
    if decision.nurse_user_id != principal.subject:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="A decision is made as yourself.")
    before = await intake.state(session_id)
    try:
        await intake.resume_nurse(session_id, decision)
    except NotWaiting as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="This session is not waiting for a nurse decision."
        ) from exc
    except InvalidNurseDecision as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    suggestion = before.get("suggestion") if before else None
    async with clinic_session(principal.clinic_id) as db:
        db.add(
            NurseDecisionRow(
                clinic_id=principal.clinic_id,
                session_id=uuid.UUID(session_id),
                decision_id=decision.decision_id,
                nurse_id=decision.nurse_user_id,
                suggestion_id=decision.suggestion_id,
                suggested_level=int(suggestion.level) if suggestion else None,
                final_level=int(decision.final_level),
                scale=decision.scale.value,
                decision=decision.action.value.lower(),
                override_reason_code=decision.override_reason_code.value
                if decision.override_reason_code
                else None,
                model_version=suggestion.model_version if suggestion else None,
                facts_snapshot=snapshot(before) if before else {},
                suggested_at=suggestion.created_at if suggestion else None,
                decided_at=decision.decided_at,
            )
        )
        token = await request.app.state.queue.session_token(db, session_id=session_id)
    await touch(principal.clinic_id, session_id)
    logger.info(
        "nurse.decision",
        extra={
            "session_id": session_id,
            "action": decision.action.value,
            "level": int(decision.final_level),
        },
    )
    # Optional fields without a value are left out (a queued token has no room yet). The token and
    # the decision's suggestion_id are required and may be null: without a triage model nothing
    # was suggested, and dropping the null made this a 500 after the decision had taken effect.
    decided = decision.model_dump(mode="json", exclude_none=True)
    decided["suggestion_id"] = decision.suggestion_id
    token_body = token.model_dump(mode="json", exclude_none=True) if token else None
    result = DecisionResult.model_validate({"decision": decided, "token": token_body})
    body = result.model_dump(mode="json", exclude_none=True)
    body["decision"]["suggestion_id"] = decision.suggestion_id
    body["token"] = token_body
    return JSONResponse(body)


def nurse_facts(session_id: str, principal: Principal, body: NurseVitalsRequest) -> list[FactEntry]:
    now = utc_now()
    source = {"kind": "nurse", "staff_user_id": principal.subject}
    values: dict[str, Any] = body.model_dump(exclude_none=True)
    entries = []
    for name, value in values.items():
        key = f"vitals.{name}"
        fact: dict[str, Any] = {
            "fact_id": stable_fact_id(session_id, key, "nurse", now.isoformat()),
            "provenance": "NURSE_CONFIRMED",
            "source": source,
            "recorded_at": now,
        }
        if name == "acvpu":
            code = str(getattr(value, "value", value))
            fact["value"] = {"system": ACVPU_SYSTEM, "code": code, "display": ACVPU_DISPLAY[code]}
        elif name == "on_supplemental_o2":
            fact["value"] = "yes" if value else "no"
        else:
            fact |= {"value": value, "unit": NURSE_UNITS[name]}
        entries.append(FactEntry.model_validate({"key": key, "fact": fact}))
    return entries


@router.post(
    "/sessions/{session_id}/vitals", response_model=TriageView, response_model_exclude_unset=True
)
async def post_nurse_vitals(
    session_id: str,
    body: NurseVitalsRequest,
    principal: Annotated[Principal, Depends(require_roles("nurse"))],
    intake: Annotated[IntakeRuntime, Depends(runtime)],
) -> TriageView:
    """The nurse's vitals complete NEWS2; the rules run at once and the full NEWS2 comes back."""
    await session_row(principal, session_id)
    if await intake.state(session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No intake for this session.")
    state = await intake.add_facts_and_evaluate(
        session_id, nurse_facts(session_id, principal, body)
    )
    await touch(principal.clinic_id, session_id)
    return triage_view(state)


@router.post(
    "/sessions/{session_id}/tasks/{task_id}/complete",
    response_model=Task,
    response_model_exclude_none=True,
)
async def complete_task(
    session_id: str,
    task_id: str,
    principal: Annotated[Principal, Depends(require_roles("nurse"))],
    intake: Annotated[IntakeRuntime, Depends(runtime)],
    request: Request,
) -> Task:
    """Mark a task (the 10-minute ECG) done: a NURSE_CONFIRMED `tasks.ecg_done_at` fact, and the
    written FHIR Task completed (or written completed later). Idempotent."""
    await session_row(principal, session_id)
    state = await intake.state(session_id)
    if state is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No intake for this session.")
    found = next((t for t in tasks(state) if t.task_id == task_id), None)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such task.")
    if found.status.value == "completed":
        return found
    now = utc_now()
    fact = {
        "fact_id": stable_fact_id(session_id, FactKey.tasks_ecg_done_at.value, "nurse"),
        "provenance": "NURSE_CONFIRMED",
        "source": {"kind": "nurse", "staff_user_id": principal.subject},
        "recorded_at": now,
        "value": now.isoformat(),
    }
    await intake.add_facts(
        session_id, [FactEntry.model_validate({"key": FactKey.tasks_ecg_done_at, "fact": fact})]
    )
    store = SqlWritebackStore()
    written = await store.resources(str(principal.clinic_id), session_id)
    fhir_task = next((w for w in written if w.local_id == f"task-{ECG_ACTION}"), None)
    if fhir_task is not None:
        handler = WritebackHandler(
            records=_NoRecords(), store=store, events=_NoEvents(), adapter=hospital_adapter()
        )
        try:
            await handler.complete_task(str(principal.clinic_id), session_id, fhir_task.resource_id)
        except (UnknownTask, Exception) as exc:  # the fact stands; the FHIR Task is retried later
            logger.warning("nurse.task_fhir", extra={"error": type(exc).__name__})
        finally:
            await handler.client.aclose()
    updated = await intake.state(session_id)
    if updated is not None and updated.get("nurse_decision") is not None:
        await request.app.state.briefings.build(updated)  # rebuilt with the ECG (M1-16)
    await touch(principal.clinic_id, session_id)
    done = next(t for t in tasks(updated or state) if t.task_id == ecg_task_id(ECG_ACTION))
    return done


class _NoRecords:
    async def load(self, clinic_id: str, session_id: str) -> None:
        return None


class _NoEvents:
    async def publish(self, event_type: str, data: Any) -> None:
        return None


__all__ = ["router"]
