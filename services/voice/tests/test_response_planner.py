import pytest

from voice_agent.workflow.response_planner import ResponsePlan, ResponsePlanner


def test_response_planner_defaults_to_streaming_llm() -> None:
    plan = ResponsePlanner().plan(user_text="I need service")

    assert plan.action == "streaming_llm"
    assert plan.confidence == 1.0
    assert plan.reason == "default_open_ended_path"


def test_response_planner_marks_blank_turns_as_clarify() -> None:
    plan = ResponsePlanner().plan(user_text="   ")

    assert plan.action == "clarify"
    assert plan.confidence == 1.0
    assert plan.reason == "blank_turn"


def test_response_plan_validates_confidence_and_reason() -> None:
    with pytest.raises(ValueError, match="confidence"):
        ResponsePlan(action="streaming_llm", confidence=1.1, reason="x")

    with pytest.raises(ValueError, match="reason"):
        ResponsePlan(action="streaming_llm", confidence=1.0, reason=" ")
