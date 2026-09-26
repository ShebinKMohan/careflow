"""Cross-checks the generated models cannot express.

The JSON Schemas carry some rules as `if`/`then` (provenance-source pairing, override reasons)
that datamodel-codegen does not turn into Pydantic validation, and some rules span fields or
objects (span offsets, unique fact ids, alert evidence). Every function returns a list of
human-readable problems; an empty list means the object passed.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Final, cast

from pydantic import BaseModel

from careflow_contracts.generated.alert_schema import Alert
from careflow_contracts.generated.fact_schema import FactBase, Provenance, TranscriptSpanSource
from careflow_contracts.generated.nurse_decision_schema import DecisionAction, NurseDecision
from careflow_contracts.generated.structured_facts_schema import StructuredFacts
from careflow_contracts.generated.urgency_suggestion_schema import UrgencySuggestion

# Safety rule 4: each provenance has exactly one kind of source.
EXPECTED_SOURCE_KIND: Final[dict[Provenance, str]] = {
    Provenance.EHR: "fhir",
    Provenance.PATIENT_STATED: "transcript_span",
    Provenance.DEVICE_MEASURED: "device",
    Provenance.NURSE_CONFIRMED: "nurse",
    Provenance.MODEL_INFERRED: "model",
}


def iter_facts(facts: StructuredFacts) -> Iterator[tuple[str, FactBase]]:
    """Yield `(path, fact)` for every fact present, in field order.

    Paths use the FactKey spelling, with list positions in brackets:
    `vitals.heart_rate`, `symptoms[0].name`, `ehr_context.allergies[1]`.
    """
    yield from _walk(facts, "")


def _walk(model: BaseModel, prefix: str) -> Iterator[tuple[str, FactBase]]:
    for name in type(model).model_fields:
        value: object = getattr(model, name)
        path = f"{prefix}{name}"
        if isinstance(value, FactBase):
            yield path, value
        elif isinstance(value, BaseModel):
            yield from _walk(value, f"{path}.")
        elif isinstance(value, list):
            for index, item in enumerate(cast(list[object], value)):
                item_path = f"{path}[{index}]"
                if isinstance(item, FactBase):
                    yield item_path, item
                elif isinstance(item, BaseModel):
                    yield from _walk(item, f"{item_path}.")


def check_provenance(facts: StructuredFacts) -> list[str]:
    """Every fact's source kind must match its provenance (EHR-fhir, PATIENT_STATED-transcript_span,
    DEVICE_MEASURED-device, NURSE_CONFIRMED-nurse, MODEL_INFERRED-model)."""
    problems: list[str] = []
    for path, fact in iter_facts(facts):
        expected = EXPECTED_SOURCE_KIND[fact.provenance]
        if fact.source.kind != expected:
            problems.append(
                f"{path}: provenance {fact.provenance.value} needs a {expected} source, "
                f"got {fact.source.kind}"
            )
    return problems


def check_transcript_spans(facts: StructuredFacts) -> list[str]:
    """Transcript spans must have end greater than start."""
    problems: list[str] = []
    for path, fact in iter_facts(facts):
        source = fact.source
        if isinstance(source, TranscriptSpanSource) and source.end <= source.start:
            problems.append(f"{path}: transcript span end {source.end} <= start {source.start}")
    return problems


def check_unique_fact_ids(facts: StructuredFacts) -> list[str]:
    """Fact ids are unique within one StructuredFacts, so evidence references are unambiguous."""
    seen: dict[str, str] = {}
    problems: list[str] = []
    for path, fact in iter_facts(facts):
        if fact.fact_id in seen:
            problems.append(f"{path}: fact_id {fact.fact_id} already used at {seen[fact.fact_id]}")
        else:
            seen[fact.fact_id] = path
    return problems


def check_structured_facts(facts: StructuredFacts) -> list[str]:
    """All StructuredFacts checks: provenance pairing, span offsets and unique fact ids."""
    return check_provenance(facts) + check_transcript_spans(facts) + check_unique_fact_ids(facts)


def _fact_ids(facts: StructuredFacts) -> set[str]:
    return {fact.fact_id for _, fact in iter_facts(facts)}


def check_alert_evidence(alert: Alert, facts: StructuredFacts) -> list[str]:
    """Every evidence fact id of the alert exists in the facts of the same session."""
    problems: list[str] = []
    if alert.session_id != facts.session_id:
        problems.append(
            f"alert {alert.alert_id} belongs to session {alert.session_id}, not {facts.session_id}"
        )
    known = _fact_ids(facts)
    problems.extend(
        f"alert {alert.alert_id}: evidence fact {fact_id} not found"
        for fact_id in alert.evidence_fact_ids
        if fact_id not in known
    )
    return problems


def check_suggestion_evidence(suggestion: UrgencySuggestion, facts: StructuredFacts) -> list[str]:
    """Every fact a suggestion reason cites exists in the facts of the same session."""
    problems: list[str] = []
    if suggestion.session_id != facts.session_id:
        problems.append(
            f"suggestion {suggestion.suggestion_id} belongs to session {suggestion.session_id}, "
            f"not {facts.session_id}"
        )
    known = _fact_ids(facts)
    for index, reason in enumerate(suggestion.reasons):
        problems.extend(
            f"suggestion {suggestion.suggestion_id}: reason {index} cites unknown fact {fact_id}"
            for fact_id in reason.fact_ids
            if fact_id not in known
        )
    return problems


def check_nurse_decision(decision: NurseDecision) -> list[str]:
    """An OVERRIDE needs an override_reason_code (the schema's if/then, re-checked here)."""
    if decision.action is DecisionAction.OVERRIDE and decision.override_reason_code is None:
        return [f"decision {decision.decision_id}: OVERRIDE needs an override_reason_code"]
    return []
