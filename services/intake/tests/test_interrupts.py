"""The graph pauses for the patient and for the nurse, and resumes with confirm or override."""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.types import Command

from careflow_contracts.generated.nurse_decision_schema import DecisionAction, OverrideReasonCode
from careflow_intake.checkpoint import memory_checkpointer
from careflow_intake.graph import (
    IntakeGraph,
    NotWaiting,
    answer,
    build_graph,
    decide,
    pending,
    start_intake,
    thread_config,
)
from careflow_intake.nodes.conversation import InvalidAnswer
from careflow_intake.nodes.triage import InvalidNurseDecision
from careflow_intake.ports import IntakePorts
from careflow_intake.stubs import START, RecordingEventSink, StubQueue, StubUrgencyModel

type Script = dict[str, Any]


async def to_nurse_review(graph: IntakeGraph, script: Script) -> dict[str, Any]:
    """Scripted answers through raw `Command(resume=...)` until the nurse review."""
    config = thread_config(script["session_id"])
    result = await start_intake(
        graph, session_id=script["session_id"], clinic_id=script["clinic_id"], arrived_at=START
    )
    for question, text in zip(script["questions"], script["answers"], strict=True):
        waiting = pending(result)
        assert waiting is not None and waiting["kind"] == "patient_input"
        assert waiting["question"]["question_id"] == question["question_id"]
        result = await graph.ainvoke(Command(resume=text), config)
    waiting = pending(result)
    assert waiting is not None and waiting["kind"] == "nurse_review"
    return result


async def test_confirm_ends_the_graph_with_a_token(
    graph: IntakeGraph, ports: IntakePorts, script: Script, make_decision: Any
) -> None:
    result = await to_nurse_review(graph, script)
    review = pending(result) or {}
    suggestion = result["suggestion"]
    assert review["suggestion"]["suggestion_id"] == suggestion.suggestion_id
    assert [alert["rule_id"] for alert in review["alerts"]] == ["CHEST_PAIN_ECG_10MIN"]
    # Nothing after the review has run yet.
    assert "token" not in result and "assign_token" not in result["node_trace"]

    sid = script["session_id"]
    done = await decide(graph, sid, make_decision(sid, suggestion))
    assert pending(done) is None and done["token"] == "A001" and done["room"] == "triage-1"
    assert done["nurse_decision"].action is DecisionAction.CONFIRM
    assert done["writeback_status"] == "stubbed" and done["briefing_ref"] == f"briefing:{sid}"
    assert (await graph.aget_state(thread_config(sid))).next == ()
    assert isinstance(ports.queue, StubQueue) and ports.queue.assigned == [(sid, suggestion.level)]
    assert isinstance(ports.events, RecordingEventSink)
    assert ports.events.types == [
        "alert.raised",
        "triage.suggested",
        "triage.confirmed",
        "intake.completed",
    ]


async def test_override_with_a_reason_stores_the_new_level_and_reason(
    graph: IntakeGraph, ports: IntakePorts, script: Script, make_decision: Any
) -> None:
    result = await to_nurse_review(graph, script)
    sid, suggestion = script["session_id"], result["suggestion"]
    override = make_decision(
        sid,
        suggestion,
        action="OVERRIDE",
        final_level=2,
        override_reason_code="clinical_judgement",
        override_note="ECG changes",
    )
    done = await decide(graph, sid, override)
    decision = done["nurse_decision"]
    assert suggestion.level == 3 and decision.final_level == 2
    assert decision.override_reason_code is OverrideReasonCode.clinical_judgement
    assert decision.override_note == "ECG changes"
    assert isinstance(ports.queue, StubQueue) and ports.queue.assigned == [(sid, 2)]
    assert isinstance(ports.events, RecordingEventSink)
    confirmed = [data for kind, data, _ in ports.events.events if kind.value == "triage.confirmed"]
    assert [data.model_dump()["final_level"] for data in confirmed] == [2]


async def test_override_without_a_reason_is_rejected_and_a_valid_resume_succeeds(
    graph: IntakeGraph, script: Script, make_decision: Any
) -> None:
    result = await to_nurse_review(graph, script)
    sid, suggestion = script["session_id"], result["suggestion"]
    with pytest.raises(InvalidNurseDecision, match="OVERRIDE needs an override_reason_code"):
        await decide(graph, sid, make_decision(sid, suggestion, action="OVERRIDE", final_level=2))
    snapshot = await graph.aget_state(thread_config(sid))
    assert snapshot.next == ("nurse_review",) and "nurse_decision" not in snapshot.values

    done = await decide(
        graph,
        sid,
        make_decision(
            sid,
            suggestion,
            action="OVERRIDE",
            final_level=2,
            override_reason_code="new_information",
        ),
    )
    assert pending(done) is None and done["nurse_decision"].final_level == 2 and done["token"]


@pytest.mark.parametrize(
    ("overrides", "problem"),
    [
        ({"final_level": 1}, "a level different from the suggestion needs a reason"),
        ({"session_id": "ses_other"}, "another session"),
        ({"suggestion_id": "sug_stale"}, "does not answer the current suggestion"),
        ({"scale": "ESI"}, "must use the KTAS scale"),
        (
            {"action": "OVERRIDE", "final_level": 2, "override_reason_code": "other"},
            "'other' needs a note",
        ),
        ({"final_level": 7}, "not a valid NurseDecision"),
    ],
)
async def test_invalid_decisions_are_rejected(
    graph: IntakeGraph, script: Script, make_decision: Any, overrides: dict[str, Any], problem: str
) -> None:
    result = await to_nurse_review(graph, script)
    sid = script["session_id"]
    invalid = {**make_decision(sid, result["suggestion"]), **overrides}
    with pytest.raises(InvalidNurseDecision, match=problem):
        await decide(graph, sid, invalid)
    assert (await graph.aget_state(thread_config(sid))).next == ("nurse_review",)


async def test_an_invalid_raw_resume_pauses_again_with_the_error(
    graph: IntakeGraph, script: Script, make_decision: Any
) -> None:
    """Resuming past `decide` (no pre-check) never stores an invalid level: the node asks again."""
    result = await to_nurse_review(graph, script)
    sid, config = script["session_id"], thread_config(script["session_id"])
    bad = make_decision(result["session_id"], result["suggestion"], action="OVERRIDE")
    again = await graph.ainvoke(Command(resume=bad), config)
    review = pending(again) or {}
    assert review["kind"] == "nurse_review" and "override_reason_code" in review["error"]
    assert "nurse_decision" not in again
    done = await decide(graph, sid, make_decision(sid, result["suggestion"]))
    assert pending(done) is None and done["token"] == "A001"


async def test_an_empty_answer_is_rejected_and_asked_again(
    graph: IntakeGraph, script: Script
) -> None:
    sid, config = script["session_id"], thread_config(script["session_id"])
    await start_intake(graph, session_id=sid, clinic_id=script["clinic_id"], arrived_at=START)
    with pytest.raises(InvalidAnswer, match="empty"):
        await answer(graph, sid, "   ")
    again = await graph.ainvoke(Command(resume=""), config)
    waiting = pending(again) or {}
    assert waiting["kind"] == "patient_input" and waiting["error"] == "the answer is empty"
    assert waiting["question"]["question_id"] == "q_complaint"
    result = await answer(graph, sid, "Tight chest")
    assert (pending(result) or {})["question"]["question_id"] == "q_chest_pain"
    assert [turn.text for turn in result["turns"] if turn.role.value == "patient"] == [
        "Tight chest"
    ]


async def test_resuming_the_wrong_step_is_refused(
    graph: IntakeGraph, script: Script, make_decision: Any
) -> None:
    sid = script["session_id"]
    await start_intake(graph, session_id=sid, clinic_id=script["clinic_id"], arrived_at=START)
    with pytest.raises(NotWaiting, match="nurse_review"):
        await decide(graph, sid, make_decision(sid, None))


async def test_without_a_suggestion_the_nurse_sets_the_level(
    make_ports: Any, script: Script, make_decision: Any
) -> None:
    ports = make_ports(urgency=StubUrgencyModel(unavailable="patient outside the training data"))
    graph = build_graph(ports, memory_checkpointer())
    result = await to_nurse_review(graph, script)
    review = pending(result) or {}
    assert review["suggestion"] is None
    assert review["suggestion_unavailable"] == "patient outside the training data"
    sid = script["session_id"]
    done = await decide(graph, sid, make_decision(sid, None, final_level=2))
    assert done["nurse_decision"].final_level == 2 and done["nurse_decision"].suggestion_id is None
    assert "triage.suggested" not in ports.events.types
