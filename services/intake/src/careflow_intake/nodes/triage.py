"""suggest_urgency and nurse_review. No level is final until a nurse resumes the graph
(safety rule 3)."""

from __future__ import annotations

import logging
from typing import Any

from langgraph.types import interrupt
from pydantic import ValidationError

from careflow_contracts.generated.ehr_fetch_schema import EhrStatus
from careflow_contracts.generated.events.envelope_schema import EventType
from careflow_contracts.generated.events.triage_confirmed_schema import TriageConfirmedData
from careflow_contracts.generated.events.triage_suggested_schema import TriageSuggestedData
from careflow_contracts.generated.nurse_decision_schema import NurseDecision, OverrideReasonCode
from careflow_contracts.generated.structured_facts_schema import NeededFactAudience
from careflow_contracts.generated.urgency_suggestion_schema import TriageScale
from careflow_contracts.validators import check_nurse_decision, check_suggestion_evidence
from careflow_intake.nodes import Update
from careflow_intake.ports import IntakePorts, NoSuggestion
from careflow_intake.state import IntakeSettings, IntakeState, structured_facts

logger = logging.getLogger(__name__)


class InvalidNurseDecision(ValueError):
    """The nurse's decision is not acceptable (e.g. a changed level without a reason)."""


async def suggest_urgency(
    state: IntakeState, ports: IntakePorts, settings: IntakeSettings
) -> Update:
    """Ask the urgency model for a suggestion. Without one (model cannot suggest, fails, or cites
    facts that are not in the session) the nurse sets the level alone."""
    facts = structured_facts(state)
    session_id = state["session_id"]
    try:
        suggestion = ports.urgency.predict(facts, scale=settings.scale, now=ports.clock())
    except NoSuggestion as exc:
        return _unavailable(str(exc) or "the model cannot suggest a level")
    except Exception as exc:  # fail safe: the nurse can always triage without a suggestion
        logger.warning("urgency model failed for %s: %s", session_id, type(exc).__name__)
        return _unavailable(f"the urgency model failed ({type(exc).__name__})")
    problems = check_suggestion_evidence(suggestion, facts)
    if suggestion.session_id != session_id:
        problems.append("the suggestion is for another session")
    if problems:
        return _unavailable("; ".join(problems))
    data = TriageSuggestedData(
        clinic_id=state["clinic_id"],
        session_id=session_id,
        suggestion_id=suggestion.suggestion_id,
        level=suggestion.level,
        scale=suggestion.scale,
        confidence=suggestion.confidence,
        model_id=suggestion.model_id,
        model_version=suggestion.model_version,
        created_at=suggestion.created_at,
    )
    key = f"{session_id}:triage.suggested:{suggestion.suggestion_id}"
    await ports.events.emit(EventType.triage_suggested, data, key)
    return {
        "suggestion": suggestion,
        "suggestion_unavailable": None,
        "node_trace": ["suggest_urgency"],
    }


def _unavailable(reason: str) -> Update:
    return {
        "suggestion": None,
        "suggestion_unavailable": reason,
        "node_trace": ["suggest_urgency"],
    }


def review_payload(state: IntakeState, scale: TriageScale) -> dict[str, Any]:
    """What the nurse sees at the interrupt."""
    suggestion = state.get("suggestion")
    return {
        "kind": "nurse_review",
        "session_id": state["session_id"],
        "scale": scale.value,
        "suggestion": suggestion.model_dump(mode="json") if suggestion else None,
        "suggestion_unavailable": state.get("suggestion_unavailable"),
        "alerts": [alert.model_dump(mode="json") for alert in state.get("alerts", [])],
        "needed_facts": [
            need.model_dump(mode="json")
            for need in state.get("needed_facts", [])
            if need.audience is NeededFactAudience.nurse
        ],
        "flags": dict(state.get("flags", {})),
        # EhrStatus(...): a StrEnum channel comes back from the Postgres checkpoint as a plain str
        "ehr_status": EhrStatus(ehr).value
        if (ehr := state.get("ehr_status")) is not None
        else None,
        "ehr_errors": list(state.get("ehr_errors", [])),
    }


def validate_decision(raw: Any, state: IntakeState, *, scale: TriageScale) -> NurseDecision:
    """A contract `NurseDecision` for this session and suggestion. OVERRIDE, or any level other
    than the suggested one, needs a reason (and "other" needs a note)."""
    try:
        decision = raw if isinstance(raw, NurseDecision) else NurseDecision.model_validate(raw)
    except ValidationError as exc:
        raise InvalidNurseDecision(
            f"not a valid NurseDecision ({exc.error_count()} errors)"
        ) from exc
    problems = check_nurse_decision(decision)
    if decision.session_id != state["session_id"]:
        problems.append("the decision is for another session")
    suggestion = state.get("suggestion")
    if decision.suggestion_id != (suggestion.suggestion_id if suggestion else None):
        problems.append("the decision does not answer the current suggestion")
    if decision.scale != scale:
        problems.append(f"the decision must use the {scale.value} scale")
    changed = suggestion is not None and decision.final_level != suggestion.level
    if changed and decision.override_reason_code is None:
        problems.append("a level different from the suggestion needs a reason")
    note = (decision.override_note or "").strip()
    if decision.override_reason_code is OverrideReasonCode.other and not note:
        problems.append("the reason 'other' needs a note")
    if problems:
        raise InvalidNurseDecision("; ".join(problems))
    return decision


async def nurse_review(state: IntakeState, ports: IntakePorts, settings: IntakeSettings) -> Update:
    """Pause for the nurse. Nothing happens before `interrupt()`; an invalid decision pauses
    again with the error (raising here would replay the same invalid value on every resume)."""
    base = review_payload(state, settings.scale)
    payload = base
    while True:
        raw = interrupt(payload)
        try:
            decision = validate_decision(raw, state, scale=settings.scale)
            break
        except InvalidNurseDecision as exc:
            payload = {**base, "error": str(exc)}
    data = TriageConfirmedData(
        clinic_id=state["clinic_id"],
        session_id=state["session_id"],
        decision_id=decision.decision_id,
        suggestion_id=decision.suggestion_id,
        action=decision.action,
        final_level=decision.final_level,
        scale=decision.scale,
        override_reason_code=decision.override_reason_code,
        nurse_user_id=decision.nurse_user_id,
        decided_at=decision.decided_at,
    )
    key = f"{state['session_id']}:triage.confirmed:{decision.decision_id}"
    await ports.events.emit(EventType.triage_confirmed, data, key)
    return {"nurse_decision": decision, "node_trace": ["nurse_review"]}
