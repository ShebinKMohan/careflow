"""The contract views of a session's intake state (M1-12): the triage card, the worklist row, tasks,
NEWS2, warnings and the patient's status. The patient's status never holds a level or an alert.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Final

from careflow_contracts.api_models import (
    AlertChip,
    FeelsWorseSummary,
    MedicalQuestion,
    News2Score,
    SessionState,
    SessionStatus,
    SessionWarning,
    Task,
    TriageView,
    WorklistItem,
)
from careflow_contracts.generated.fact_schema import NurseSource
from careflow_contracts.generated.structured_facts_schema import FactKey, NeededFactAudience
from careflow_intake.facts import FactEntry
from careflow_intake.state import IntakeState, structured_facts
from careflow_rules.news2 import News2Result, news2_inputs, score_news2

ECG_ACTION: Final = "ecg_within_10_min"
QUEUE_STATES: Final = {
    "waiting": SessionState.queued,
    "called": SessionState.called,
    "arrived": SessionState.arrived,
    "no_show": SessionState.no_show,
    "closed": SessionState.closed,
}


def task_id(action: str) -> str:
    return f"tsk_{action}"


def news2(state: IntakeState) -> News2Score | None:
    result: News2Result | None = score_news2(news2_inputs(structured_facts(state)))
    if result is None:
        return None
    return News2Score.model_validate(
        {
            "total": result.total,
            "subscores": dict(result.subscores),
            "missing": [key.value for key in result.missing],
            "partial": result.partial,
            "any_single_3": result.any_single_3,
            "risk_band": result.risk_band,
        }
    )


def ecg_done(state: IntakeState) -> FactEntry | None:
    return next((e for e in state.get("facts", []) if e.key is FactKey.tasks_ecg_done_at), None)


def tasks(state: IntakeState) -> list[Task]:
    """A task for each alert whose required action has a deadline (today: the ECG)."""
    found: list[Task] = []
    done = ecg_done(state)
    for alert in state.get("alerts", []):
        if alert.required_action != ECG_ACTION or alert.deadline_at is None:
            continue
        body: dict[str, Any] = {
            "task_id": task_id(ECG_ACTION),
            "session_id": state["session_id"],
            "kind": ECG_ACTION,
            "status": "completed" if done else "open",
            "due_at": alert.deadline_at,
        }
        if done is not None:
            body["completed_at"] = done.recorded_at
            if isinstance(done.base.source, NurseSource):
                body["completed_by"] = done.base.source.staff_user_id
        found.append(Task.model_validate(body))
    return found


def warnings(state: IntakeState) -> list[SessionWarning]:
    flags = state.get("flags", {})
    found = []
    if flags.get("extraction_incomplete"):
        found.append(SessionWarning.extraction_incomplete)
    if flags.get("ehr_unavailable"):
        found.append(SessionWarning.ehr_unavailable)
    if flags.get("intake_truncated"):
        found.append(SessionWarning.intake_stopped_early)
    return found


def session_state(
    state: IntakeState | None, waiting: str | None, token_status: str | None
) -> SessionState:
    if token_status is not None:
        return QUEUE_STATES.get(token_status, SessionState.queued)
    if state is not None and state.get("nurse_decision") is not None:
        return SessionState.queued
    if waiting == "nurse_review":
        return SessionState.awaiting_nurse
    return SessionState.intake


def triage_view(state: IntakeState) -> TriageView:
    decision = state.get("nurse_decision")
    needed = [
        need for need in state.get("needed_facts", []) if need.audience is NeededFactAudience.nurse
    ]
    body: dict[str, Any] = {
        "session_id": state["session_id"],
        "status": "confirmed" if decision is not None else "awaiting_nurse",
        "alerts": [a.model_dump(mode="json", exclude_none=True) for a in state.get("alerts", [])],
        "suggestion": (
            s.model_dump(mode="json", exclude_none=True) if (s := state.get("suggestion")) else None
        ),
        "needed_facts": [n.model_dump(mode="json", exclude_none=True) for n in needed],
        "news2": score.model_dump(mode="json", exclude_none=True)
        if (score := news2(state))
        else None,
        "open_tasks": [
            t.model_dump(mode="json", exclude_none=True)
            for t in tasks(state)
            if t.status.value == "open"
        ],
        "warnings": [w.value for w in warnings(state)],
    }
    if decision is not None:
        body["decision"] = decision.model_dump(mode="json", exclude_none=True)
    return TriageView.model_validate(body)


def complaint(state: IntakeState) -> str | None:
    entry = next((e for e in state.get("facts", []) if e.key is FactKey.chief_complaint), None)
    if entry is None:
        return None
    text = str(getattr(entry.fact, "value", ""))
    return text[:77] + "..." if len(text) > 80 else text or None


def worklist_item(
    state: IntakeState,
    *,
    patient_display: str,
    waiting: str | None,
    token_code: str | None,
    token_status: str | None,
    feels_worse: FeelsWorseSummary | None = None,
    medical_questions: Sequence[MedicalQuestion] | None = None,
) -> WorklistItem:
    facts = structured_facts(state)
    patient = facts.patient
    suggestion, decision = state.get("suggestion"), state.get("nurse_decision")
    body: dict[str, Any] = {
        "session_id": state["session_id"],
        "patient_display": patient_display[:8],
        "arrived_at": state["arrived_at"],
        "state": session_state(state, waiting, token_status).value,
        "alerts": [
            AlertChip(
                alert_id=a.alert_id, rule_id=a.rule_id, severity=a.severity, partial=a.partial
            ).model_dump(mode="json")
            for a in state.get("alerts", [])
        ],
        "open_tasks": [
            t.model_dump(mode="json", exclude_none=True)
            for t in tasks(state)
            if t.status.value == "open"
        ],
        "warnings": [w.value for w in warnings(state)],
    }
    if token_code:
        body["token_code"] = token_code
    if suggestion is not None:
        body["suggested_level"] = int(suggestion.level)
        body["scale"] = suggestion.scale.value
    if decision is not None:
        body["confirmed_level"] = int(decision.final_level)
        body["scale"] = decision.scale.value
    if patient is not None and patient.age_years is not None:
        body["age_years"] = min(int(patient.age_years.value), 130)
    if patient is not None and patient.sex is not None:
        code = str(patient.sex.value.code)
        if code in ("male", "female", "other", "unknown"):
            body["sex"] = code
    if (text := complaint(state)) is not None:
        body["complaint"] = text
    if feels_worse is not None:  # M2-07: the waiting patient's presses, for this nurse only
        body["feels_worse"] = feels_worse.model_dump(mode="json", exclude_none=True)
    if medical_questions:  # M2-09: refused on the phone and flagged, for this nurse only
        body["medical_questions"] = [q.model_dump(mode="json") for q in medical_questions]
    return WorklistItem.model_validate(body)


def patient_status(
    state: IntakeState | None,
    *,
    waiting: str | None,
    token_code: str | None,
    token_status: str | None,
    room: str | None,
    wait_estimate_min: int | None,
) -> SessionStatus:
    """What the patient may see: the state, the token, the room and the wait. Never a level or an
    alert (safety rules 2 and 3)."""
    body: dict[str, Any] = {"state": session_state(state, waiting, token_status).value}
    if token_code:
        body["token_code"] = token_code
    if room:
        body["room"] = room[:32]
    if wait_estimate_min is not None:
        body["wait_estimate_min"] = max(0, wait_estimate_min)
    return SessionStatus.model_validate(body)


def now_minutes(start: datetime, end: datetime) -> int:
    return max(0, round((end - start).total_seconds() / 60))


__all__ = [
    "ECG_ACTION",
    "ecg_done",
    "news2",
    "patient_status",
    "session_state",
    "task_id",
    "tasks",
    "triage_view",
    "warnings",
    "worklist_item",
]
