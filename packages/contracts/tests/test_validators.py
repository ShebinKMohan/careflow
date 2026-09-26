"""Hand-written cross-checks in careflow_contracts.validators."""

from __future__ import annotations

import re

import pytest
from careflow_contracts import (
    Alert,
    FactKey,
    NurseDecision,
    StructuredFacts,
    TranscriptSpanSource,
    UrgencySuggestion,
)
from careflow_contracts.testing import load_model
from careflow_contracts.validators import (
    check_alert_evidence,
    check_nurse_decision,
    check_provenance,
    check_structured_facts,
    check_suggestion_evidence,
    check_transcript_spans,
    check_unique_fact_ids,
    iter_facts,
)

PERSONAS = ("chest_pain", "stroke", "child_drowsy", "sepsis_partial", "routine")
ALERTING = ("chest_pain", "stroke", "child_drowsy", "sepsis_partial")


def facts_of(persona: str) -> StructuredFacts:
    return load_model(f"valid/{persona}.structured-facts.json", StructuredFacts)


@pytest.mark.parametrize("persona", PERSONAS)
def test_valid_facts_pass_every_check(persona: str) -> None:
    facts = facts_of(persona)
    assert check_provenance(facts) == []
    assert check_structured_facts(facts) == []


def test_mismatched_source_gives_exactly_one_provenance_message() -> None:
    # Pydantic does not enforce the schema's if/then, so the model loads and the check catches it.
    facts = load_model("invalid/patient_stated_fhir_source.structured-facts.json", StructuredFacts)
    problems = check_provenance(facts)
    assert len(problems) == 1
    assert problems[0].startswith("chief_complaint: provenance PATIENT_STATED")


@pytest.mark.parametrize("persona", ALERTING)
def test_alert_evidence_exists(persona: str) -> None:
    alert = load_model(f"valid/{persona}.alert.json", Alert)
    assert check_alert_evidence(alert, facts_of(persona)) == []


def test_alert_evidence_must_exist_and_match_the_session() -> None:
    alert = load_model("valid/stroke.alert.json", Alert)
    other = facts_of("routine")
    problems = check_alert_evidence(alert, other)
    assert any("belongs to session ses_stroke" in p for p in problems)
    assert sum("not found" in p for p in problems) == len(alert.evidence_fact_ids)


@pytest.mark.parametrize("persona", PERSONAS)
def test_suggestion_reasons_cite_known_facts(persona: str) -> None:
    suggestion = load_model(f"valid/{persona}.urgency-suggestion.json", UrgencySuggestion)
    assert check_suggestion_evidence(suggestion, facts_of(persona)) == []


@pytest.mark.parametrize("persona", PERSONAS)
def test_valid_decisions_pass(persona: str) -> None:
    decision = load_model(f"valid/{persona}.nurse-decision.json", NurseDecision)
    assert check_nurse_decision(decision) == []


def test_override_without_reason_is_caught_after_model_validation() -> None:
    decision = load_model("invalid/override_without_reason.nurse-decision.json", NurseDecision)
    assert check_nurse_decision(decision) == [
        "decision dec_routine: OVERRIDE needs an override_reason_code"
    ]


def test_iter_facts_paths_are_fact_keys() -> None:
    keys = {key.value for key in FactKey}
    for persona in PERSONAS:
        for path, _ in iter_facts(facts_of(persona)):
            assert re.sub(r"\[\d+\].*$", "", path) in keys, path


def test_iter_facts_walks_lists_and_groups() -> None:
    paths = [path for path, _ in iter_facts(facts_of("sepsis_partial"))]
    assert "symptoms[0].name" in paths
    assert "symptoms[0].onset" in paths
    assert "ehr_context.conditions[0]" in paths
    assert "vitals.heart_rate" in paths
    assert "patient.age_years" in paths


def test_fixture_spans_quote_the_turn_text_length() -> None:
    for persona in PERSONAS:
        for path, fact in iter_facts(facts_of(persona)):
            if isinstance(fact.source, TranscriptSpanSource):
                assert fact.source.end - fact.source.start == len(fact.source.quote), path


def test_span_end_must_follow_start() -> None:
    facts = facts_of("routine")
    assert facts.pain_score is not None
    source = facts.pain_score.source
    assert isinstance(source, TranscriptSpanSource)
    source.end = source.start
    assert check_transcript_spans(facts) == [
        f"pain_score: transcript span end {source.start} <= start {source.start}"
    ]


def test_duplicate_fact_ids_are_reported() -> None:
    facts = facts_of("routine")
    assert facts.vitals is not None and facts.vitals.spo2 is not None
    assert facts.vitals.heart_rate is not None
    facts.vitals.spo2.fact_id = facts.vitals.heart_rate.fact_id
    assert len(check_unique_fact_ids(facts)) == 1
