"""Checkpointed state round-trips through the strict serializer; other types are blocked."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from careflow_contracts.generated.alert_schema import Alert
from careflow_contracts.generated.nurse_decision_schema import NurseDecision
from careflow_contracts.generated.structured_facts_schema import Turn
from careflow_contracts.generated.urgency_suggestion_schema import UrgencySuggestion
from careflow_intake.checkpoint import libpq_url, serializer, state_types
from careflow_intake.facts import FactEntry
from careflow_intake.graph import IntakeGraph, answer, decide, start_intake
from careflow_intake.ports import Question
from careflow_intake.stubs import START


async def finished(
    graph: IntakeGraph, script: dict[str, Any], make_decision: Any
) -> dict[str, Any]:
    sid = script["session_id"]
    result = await start_intake(
        graph, session_id=sid, clinic_id=script["clinic_id"], arrived_at=START
    )
    for text in script["answers"]:
        result = await answer(graph, sid, text)
    return await decide(graph, sid, make_decision(sid, result["suggestion"]))


async def test_the_whole_state_round_trips(
    graph: IntakeGraph, script: dict[str, Any], make_decision: Any
) -> None:
    state = await finished(graph, script, make_decision)
    serde = serializer()
    restored = serde.loads_typed(serde.dumps_typed(state))
    assert restored == state
    assert all(isinstance(entry, FactEntry) for entry in restored["facts"])
    assert all(isinstance(turn, Turn) for turn in restored["turns"])
    assert isinstance(restored["alerts"][0], Alert)
    assert isinstance(restored["suggestion"], UrgencySuggestion)
    assert isinstance(restored["nurse_decision"], NurseDecision)


def test_the_allowlist_names_the_state_models() -> None:
    names = {cls.__name__ for cls in state_types()}
    for model in (FactEntry, Question, Turn, Alert, UrgencySuggestion, NurseDecision):
        assert model.__name__ in names


class Foreign(BaseModel):
    x: int


def test_a_type_outside_the_allowlist_is_not_rebuilt() -> None:
    serde = serializer()
    restored = serde.loads_typed(serde.dumps_typed({"value": Foreign(x=1)}))
    assert not isinstance(restored["value"], Foreign)


def test_libpq_url() -> None:
    assert libpq_url("postgresql+psycopg://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"
    assert libpq_url("postgresql://u:p@h/db") == "postgresql://u:p@h/db"
