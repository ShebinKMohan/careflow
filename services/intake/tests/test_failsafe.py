"""Failures of optional steps never stop the intake, and every step's events fire once."""

from __future__ import annotations

from typing import Any

import pytest

from careflow_contracts.generated.structured_facts_schema import StructuredFacts
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


async def test_an_unavailable_ehr_is_flagged_and_the_intake_goes_on(
    make_ports: Any, script: dict[str, Any]
) -> None:
    result = await run(make_ports, script, ehr=StubEhrReader(down=True))
    assert result["flags"]["ehr_unavailable"] is True and result["ehr_context"] is None
    unidentified = await run(make_ports, script, identifier=StubIdentifier(patient_ref=None))
    assert unidentified["flags"]["ehr_unavailable"] is True


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
