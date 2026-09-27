"""The intake graph's state and its reducers.

Fields that several nodes (or outside callers) add to are merged by reducers: turns and the node
trace append, facts merge by fact id, alerts merge by rule id and flags only ever turn on.
"""

from __future__ import annotations

import operator
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal, Required, TypedDict

from careflow_contracts.generated.alert_schema import Alert
from careflow_contracts.generated.ehr_fetch_schema import (
    EhrAppointment,
    EhrStatus,
    PatientIdentifier,
)
from careflow_contracts.generated.nurse_decision_schema import NurseDecision
from careflow_contracts.generated.structured_facts_schema import (
    EhrContext,
    ExtractionStatus,
    Language,
    NeededFact,
    StructuredFacts,
    Turn,
)
from careflow_contracts.generated.turn_intent_schema import TurnIntent
from careflow_contracts.generated.urgency_suggestion_schema import TriageScale, UrgencySuggestion
from careflow_intake.facts import FactEntry, merge_facts_by_id, to_structured_facts
from careflow_intake.ports import MedicalCategory, NoticeKind, Question


class Flags(TypedDict, total=False):
    extraction_incomplete: bool
    ehr_unavailable: bool
    intake_truncated: bool


class ExtractionInfo(TypedDict, total=False):
    """The latest extraction run: what the briefing's data-quality section reports (M1-16)."""

    dropped_count: int
    model_id: str | None
    prompt_version: str | None


def merge_alerts_by_rule_id(
    existing: Sequence[Alert] | None, new: Sequence[Alert | dict[str, Any]] | Alert | None
) -> list[Alert]:
    """LangGraph reducer: an alert replaces the one with the same rule id; new rule ids append.
    Alerts are never removed: a later evaluation without the alert does not retract it.

    A replacement keeps the time the alert was first raised: every evaluation stamps `raised_at`
    with its own clock, but `alert.raised` goes out once, on the first raise, and the briefing,
    the FHIR Flag and the audit report when the nurse was told."""
    merged = {alert.rule_id: alert for alert in existing or []}
    incoming = [new] if isinstance(new, Alert) else list(new or [])
    for value in incoming:
        alert = value if isinstance(value, Alert) else Alert.model_validate(value)
        first = merged.get(alert.rule_id)
        if first is not None and first.raised_at < alert.raised_at:
            alert = alert.model_copy(update={"raised_at": first.raised_at})
        merged[alert.rule_id] = alert
    return list(merged.values())


def merge_flags(existing: Flags | None, new: Flags | None) -> Flags:
    """LangGraph reducer: a flag, once on, stays on."""
    merged = {name: bool(value) for name, value in (existing or {}).items()}
    for name, value in (new or {}).items():
        merged[name] = merged.get(name, False) or bool(value)
    return Flags(**merged)


class IntakeState(TypedDict, total=False):
    # Given when the intake starts.
    session_id: Required[str]
    clinic_id: Required[str]
    language: Required[Language]
    arrived_at: Required[datetime]
    # Who the patient is (M2-04): "child" (under 16) when a parent checks in a child; absent is an
    # adult. Set once, by the kiosk's "Who is the patient?" answer.
    patient_type: Literal["adult", "child"]
    # Where the patient answers (M2-06): the kiosk, or their phone after the QR hand-over.
    channel: Literal["kiosk", "phone"]
    # Set by the nodes.
    started_at: datetime
    patient_identifier: PatientIdentifier | None
    patient_ref: str | None
    ehr_status: EhrStatus | None
    ehr_errors: list[str]
    ehr_context: EhrContext | None
    appointments: list[EhrAppointment]
    turns: Annotated[list[Turn], operator.add]
    turn_count: int
    pending_question: Question | None
    # The question the latest patient turn answered: re-asked word for word after a turn that
    # only asked something (M2-10).
    answered_question: Question | None
    last_answer_mode: str | None  # how the latest answer came: "tap", "text" or "voice"
    # Side questions (M2-10): what the latest patient turn held, whether a side question still
    # waits for its reply, the medical layer that decided, how many were handled, the patient
    # turns that were only questions (not answers), and the reply shown before the next question.
    turn_intent: TurnIntent | None
    side_question_pending: bool
    side_category: MedicalCategory | None
    side_questions: int
    side_turn_ids: Annotated[list[str], operator.add]
    reply_prefix: str | None
    reply_kind: NoticeKind | None
    reply_citations: list[dict[str, str]]
    conversation_complete: bool
    facts: Annotated[list[FactEntry], merge_facts_by_id]
    alerts: Annotated[list[Alert], merge_alerts_by_rule_id]
    needed_facts: list[NeededFact]
    ruleset_version: str | None
    extraction: ExtractionInfo
    suggestion: UrgencySuggestion | None
    suggestion_unavailable: str | None
    nurse_decision: NurseDecision | None
    token: str | None
    room: str | None
    writeback_status: str | None
    briefing_ref: str | None
    flags: Annotated[Flags, merge_flags]
    node_trace: Annotated[list[str], operator.add]


@dataclass(frozen=True)
class IntakeSettings:
    """Caps on the patient conversation, and the scale the suggestion is displayed in."""

    max_turns: int = 8
    max_seconds: float = 120.0
    scale: TriageScale = TriageScale.KTAS


def structured_facts(state: IntakeState) -> StructuredFacts:
    """The contract `StructuredFacts` for the current state."""
    incomplete = state.get("flags", {}).get("extraction_incomplete", False)
    return to_structured_facts(
        session_id=state["session_id"],
        clinic_id=state["clinic_id"],
        language=state["language"],
        entries=state.get("facts", []),
        ehr_context=state.get("ehr_context"),
        extraction_status=ExtractionStatus.incomplete if incomplete else ExtractionStatus.complete,
    )


__all__ = [
    "ExtractionInfo",
    "Flags",
    "IntakeSettings",
    "IntakeState",
    "merge_alerts_by_rule_id",
    "merge_flags",
    "structured_facts",
]
