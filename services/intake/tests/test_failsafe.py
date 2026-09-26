"""Failures of optional steps never stop the intake, and every step's events fire once."""

from __future__ import annotations

from typing import Any

import pytest

from careflow_contracts.generated.ehr_fetch_schema import EhrFetch, EhrStatus
from careflow_contracts.generated.structured_facts_schema import (
    EhrContext,
    FactKey,
    Patient,
    StructuredFacts,
)
from careflow_contracts.generated.urgency_suggestion_schema import TriageScale, UrgencySuggestion
from careflow_intake.checkpoint import memory_checkpointer
from careflow_intake.graph import answer, build_graph, decide, pending, start_intake
from careflow_intake.stubs import (
    START,
    AnswerExtractor,
    RecordingEventSink,
    RecordingRulesRunner,
    StubEhrReader,
    StubIdentifier,
    StubUrgencyModel,
    keypad,
    stable_fact_id,
)


async def run(make_ports: Any, script: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    ports = make_ports(**overrides)
    graph = build_graph(ports, memory_checkpointer())
    sid = script["session_id"]
    result = await start_intake(
        graph, session_id=sid, clinic_id=script["clinic_id"], arrived_at=START
    )
    for text in script["answers"]:
        result = await answer(graph, sid, text)
    assert (pending(result) or {})["kind"] == "nurse_review"
    result["_ports"], result["_graph"] = ports, graph
    return result


@pytest.mark.parametrize(
    ("ehr", "identifier", "status"),
    [
        (StubEhrReader(status=EhrStatus.unavailable), keypad(), "unavailable"),
        (StubEhrReader(status=EhrStatus.not_found), keypad(), "not_found"),
        (StubEhrReader(status=EhrStatus.ambiguous), keypad(), "ambiguous"),
        (StubEhrReader(raises=True), keypad(), "unavailable"),
        (StubEhrReader(), None, "not_found"),  # an anonymous check-in has no EHR record to read
    ],
    ids=["unavailable", "not_found", "ambiguous", "reader_crashes", "anonymous"],
)
async def test_without_an_ehr_record_the_intake_goes_on_and_the_nurse_sees_why(
    make_ports: Any, script: dict[str, Any], ehr: StubEhrReader, identifier: Any, status: str
) -> None:
    result = await run(
        make_ports, script, ehr=ehr, identifier=StubIdentifier(identifier=identifier)
    )
    assert result["flags"]["ehr_unavailable"] is True and result["ehr_context"] is None
    review = pending(result) or {}
    assert review["ehr_status"] == status and review["ehr_errors"]  # never a silent empty record


async def test_a_partial_record_is_used_and_marked(make_ports: Any, script: dict[str, Any]) -> None:
    context = EhrContext.model_validate({"conditions": [condition()]})
    result = await run(
        make_ports, script, ehr=StubEhrReader(status=EhrStatus.partial, context=context)
    )
    assert "ehr_unavailable" not in result.get("flags", {})
    assert result["ehr_context"] == context and result["patient_ref"] == "Patient/synthetic-0001"
    assert (pending(result) or {})["ehr_status"] == "partial"


def condition() -> dict[str, Any]:
    return {
        "fact_id": stable_fact_id("Condition", "c1"),
        "provenance": "EHR",
        "source": {"kind": "fhir", "resource_type": "Condition", "id": "c1", "version_id": "1"},
        "recorded_at": "2026-09-27T09:00:00Z",
        "value": {
            "system": "http://snomed.info/sct",
            "code": "38341003",
            "display": "Hypertension",
        },
    }


class DemographicsReader(StubEhrReader):
    async def fetch_context(self, identifier: Any, clinic_id: str) -> EhrFetch:
        fetch = await super().fetch_context(identifier, clinic_id)
        age = {**condition(), "fact_id": stable_fact_id("Patient", "p1"), "value": 58}
        age["source"] = {"kind": "fhir", "resource_type": "Patient", "id": "p1"}
        return fetch.model_copy(update={"patient": Patient.model_validate({"age_years": age})})


async def test_ehr_demographics_become_facts(make_ports: Any, script: dict[str, Any]) -> None:
    result = await run(make_ports, script, ehr=DemographicsReader())
    [age] = [e for e in result["facts"] if e.key is FactKey.patient_age_years]
    assert age.provenance.value == "EHR" and age.base.model_dump()["value"] == 58


@pytest.mark.parametrize(
    "extractor",
    [AnswerExtractor(fail=True), AnswerExtractor(incomplete=True)],
    ids=["raises", "incomplete"],
)
async def test_a_failed_extraction_still_runs_the_rules(
    make_ports: Any, script: dict[str, Any], make_decision: Any, extractor: AnswerExtractor
) -> None:
    rules = RecordingRulesRunner()
    result = await run(make_ports, script, extractor=extractor, rules=rules)
    assert result["flags"]["extraction_incomplete"] is True
    assert len(rules.seen) == 4  # after each answer and once more before the suggestion
    assert all(facts.extraction_status.value == "incomplete" for facts in rules.seen)
    sid = script["session_id"]
    await decide(result["_graph"], sid, make_decision(sid, result["suggestion"]))
    events: RecordingEventSink = result["_ports"].events
    completed = [data for kind, data, _ in events.events if kind.value == "intake.completed"]
    assert [data.model_dump()["extraction_status"].value for data in completed] == ["incomplete"]


class BrokenModel(StubUrgencyModel):
    def predict(
        self, facts: StructuredFacts, *, scale: TriageScale = TriageScale.KTAS, now: Any = None
    ) -> UrgencySuggestion:
        raise MemoryError


class UnfoundedModel(StubUrgencyModel):
    """Cites a fact the session does not have."""

    def predict(
        self, facts: StructuredFacts, *, scale: TriageScale = TriageScale.KTAS, now: Any = None
    ) -> UrgencySuggestion:
        suggestion = super().predict(facts, scale=scale, now=now)
        reason = {"text": "Made up", "fact_ids": ["f_00000000000000000000000000"]}
        return suggestion.model_copy(
            update={"reasons": [type(suggestion.reasons[0]).model_validate(reason)]}
        )


async def test_without_a_usable_suggestion_the_nurse_still_reviews(
    make_ports: Any, script: dict[str, Any]
) -> None:
    broken = await run(make_ports, script, urgency=BrokenModel())
    assert broken["suggestion"] is None
    assert broken["suggestion_unavailable"] == "the urgency model failed (MemoryError)"
    unfounded = await run(make_ports, script, urgency=UnfoundedModel())
    assert unfounded["suggestion"] is None
    assert "f_00000000000000000000000000" in unfounded["suggestion_unavailable"]
    assert "triage.suggested" not in unfounded["_ports"].events.types


async def test_alert_raised_is_published_once_per_rule(
    make_ports: Any, script: dict[str, Any]
) -> None:
    result = await run(make_ports, script)
    events: RecordingEventSink = result["_ports"].events
    raised = [key for kind, _, key in events.events if kind.value == "alert.raised"]
    assert raised == [f"{script['session_id']}:CHEST_PAIN_ECG_10MIN"]
    # The rule fired on three runs (after the 2nd and 3rd answers and before the suggestion),
    # but only the first published: later runs see the rule id in the state.
    assert events.attempts == 2  # alert.raised once, triage.suggested once
    assert [a.rule_id.value for a in result["alerts"]] == ["CHEST_PAIN_ECG_10MIN"]
