"""Adaptive reception uses triage; registration-only visits and children keep their guards."""

from __future__ import annotations

from typing import Any

import pytest

from careflow_contracts.generated.structured_facts_schema import Language
from careflow_intake.nodes.triage import CHILD_NO_SUGGESTION, suggest_urgency
from careflow_intake.state import IntakeSettings, IntakeState
from careflow_intake.stubs import START, StubUrgencyModel, stub_ports


def state(context: dict[str, Any] | None, *, child: bool = False) -> IntakeState:
    result: IntakeState = {
        "session_id": "ses_adaptive_model",
        "clinic_id": "clinic_a",
        "language": Language.en,
        "arrived_at": START,
        "patient_type": "child" if child else "adult",
    }
    if context is not None:
        result["reception_context"] = context
    return result


@pytest.mark.parametrize("context", [{}, {"intake_mode": "registration"}])
async def test_registration_keeps_model_out_of_submission(context: dict[str, Any]) -> None:
    urgency = StubUrgencyModel()
    result = await suggest_urgency(state(context), stub_ports(urgency=urgency), IntakeSettings())
    assert urgency.calls == 0
    assert result["suggestion"] is None
    assert "Reception registration" in result["suggestion_unavailable"]


@pytest.mark.parametrize("context", [None, {"intake_mode": "adaptive"}])
async def test_adaptive_and_legacy_adults_receive_the_models_real_suggestion(
    context: dict[str, Any] | None,
) -> None:
    urgency = StubUrgencyModel(level=2)
    result = await suggest_urgency(state(context), stub_ports(urgency=urgency), IntakeSettings())
    assert urgency.calls == 1
    assert result["suggestion"].level == 2
    assert result["suggestion"].model_id == "stub-urgency"
    assert result["suggestion_unavailable"] is None
    assert "nurse_decision" not in result


async def test_adaptive_child_still_cannot_use_adult_model() -> None:
    urgency = StubUrgencyModel()
    result = await suggest_urgency(
        state({"intake_mode": "adaptive"}, child=True),
        stub_ports(urgency=urgency),
        IntakeSettings(),
    )
    assert urgency.calls == 0
    assert result["suggestion"] is None
    assert result["suggestion_unavailable"] == CHILD_NO_SUGGESTION


async def test_adaptive_model_unavailable_preserves_nurse_decision_path() -> None:
    result = await suggest_urgency(
        state({"intake_mode": "adaptive"}),
        stub_ports(urgency=StubUrgencyModel(unavailable="model unavailable")),
        IntakeSettings(),
    )
    assert result["suggestion"] is None
    assert result["suggestion_unavailable"] == "model unavailable"


async def test_anonymous_urgent_help_never_waits_for_an_urgency_model() -> None:
    urgency = StubUrgencyModel()
    urgent_state = state(None)
    urgent_state.pop("patient_type", None)
    urgent_state["urgent_help"] = {"reason": "Immediate help requested"}
    result = await suggest_urgency(urgent_state, stub_ports(urgency=urgency), IntakeSettings())
    assert urgency.calls == 0
    assert result["suggestion"] is None
    assert "immediate nurse assessment" in result["suggestion_unavailable"]
