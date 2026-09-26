"""The conversation stops after 8 patient turns or 120 s, with `intake_truncated`."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from careflow_contracts.generated.structured_facts_schema import TurnRole
from careflow_intake.checkpoint import memory_checkpointer
from careflow_intake.graph import IntakeGraph, answer, build_graph, pending, start_intake
from careflow_intake.stubs import START, EndlessConversation, SteppingClock, stub_ports


async def answer_until_nurse_review(graph: IntakeGraph, session_id: str) -> dict[str, Any]:
    result = await start_intake(
        graph, session_id=session_id, clinic_id="clinic_a", arrived_at=START
    )
    for _ in range(50):  # far beyond any cap: the graph, not the loop, must stop
        waiting = pending(result) or {}
        if waiting.get("kind") != "patient_input":
            return result
        result = await answer(graph, session_id, "I am not sure")
    raise AssertionError("the conversation never stopped")


def patient_turns(result: dict[str, Any]) -> int:
    return sum(1 for turn in result["turns"] if turn.role is TurnRole.patient)


async def test_an_endless_conversation_stops_after_8_turns() -> None:
    graph = build_graph(stub_ports(conversation=EndlessConversation()), memory_checkpointer())
    result = await answer_until_nurse_review(graph, "ses_endless")
    assert (pending(result) or {})["kind"] == "nurse_review"
    assert patient_turns(result) == 8 and result["turn_count"] == 8
    assert result["flags"]["intake_truncated"] is True
    assert (pending(result) or {})["flags"]["intake_truncated"] is True  # the nurse sees it
    trace = result["node_trace"]
    assert trace.index("suggest_urgency") > max(i for i, n in enumerate(trace) if n == "extract")


async def test_the_conversation_stops_after_120_seconds() -> None:
    clock = SteppingClock(step=timedelta(seconds=15))  # every clock read is 15 s later
    ports = stub_ports(conversation=EndlessConversation(), clock=clock)
    graph = build_graph(ports, memory_checkpointer())
    result = await answer_until_nurse_review(graph, "ses_slow")
    assert result["flags"]["intake_truncated"] is True
    assert 0 < patient_turns(result) < 8
    elapsed = (clock.now - result["started_at"]).total_seconds()
    assert elapsed >= 120


async def test_a_conversation_that_completes_is_not_truncated(
    graph: IntakeGraph, script: dict[str, Any]
) -> None:
    sid = script["session_id"]
    result = await start_intake(graph, session_id=sid, clinic_id="clinic_a", arrived_at=START)
    for text in script["answers"]:
        result = await answer(graph, sid, text)
    assert result["conversation_complete"] is True
    assert "intake_truncated" not in result.get("flags", {})
