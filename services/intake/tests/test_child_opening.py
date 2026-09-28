"""A child identified before intake gives a complaint, then the complete fixed safety bank."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest

from careflow_contracts.generated.structured_facts_schema import FactKey, Language, Turn, TurnRole
from careflow_intake.checkpoint import memory_checkpointer
from careflow_intake.conversation import ChecklistConversation
from careflow_intake.conversation.child_questions import CHILD_BANK
from careflow_intake.facts import FactEntry
from careflow_intake.graph import answer, build_graph, pending, thread_config
from careflow_intake.patient_details import patient_turns_and_facts
from careflow_intake.ports import ExtractionResult
from careflow_intake.state import IntakeState
from careflow_intake.stubs import START, StubUrgencyModel, stub_ports
from careflow_intake.tap import answer_fact
from careflow_intake.wiring import CareflowRules


@dataclass
class ComplaintExtractor:
    fail: bool = False
    calls: list[str] = field(default_factory=list[str])

    async def extract(
        self,
        turns: Sequence[Turn],
        existing_facts: Sequence[FactEntry],
        question_keys: Mapping[str, FactKey],
        language: Language = Language.en,
    ) -> ExtractionResult:
        self.calls.append(turns[-1].text)
        assert question_keys.get(turns[-1].turn_id) is FactKey.chief_complaint
        if self.fail:
            raise TimeoutError("synthetic complaint extraction failure")
        complaint = answer_fact(turns[-1], FactKey.chief_complaint, turns[-1].text)
        assert complaint is not None
        return ExtractionResult(facts=[complaint], provider="test")


def child_state(*, existing_complaint: bool = False, urgent: bool = False) -> IntakeState:
    turns, facts = patient_turns_and_facts(
        language=Language.en,
        turn_number=1,
        patient_type="child",
        age_years=4,
        age_months=None,
        now=START,
    )
    for key, text, value in (
        (FactKey.chief_complaint, "Fever since yesterday", "Fever since yesterday"),
        (FactKey.child_danger_hard_to_wake, "Hard to wake: Yes", "yes"),
    ):
        if (key is FactKey.chief_complaint and not existing_complaint) or (
            key is FactKey.child_danger_hard_to_wake and not urgent
        ):
            continue
        turn = Turn(
            turn_id=f"trn_registration_{len(turns)}",
            role=TurnRole.patient,
            text=text,
            created_at=START,
        )
        fact = answer_fact(turn, key, value)
        assert fact is not None
        turns.append(turn)
        facts.append(fact)
    return {
        "session_id": "ses_child_adaptive_opening",
        "clinic_id": "clinic_a",
        "arrived_at": START,
        "language": Language.en,
        "patient_type": "child",
        "reception_context": {"intake_mode": "adaptive", "reason": ""},
        "turns": turns,
        "facts": facts,
    }


def current_key(result: dict[str, Any]) -> str | None:
    return ((pending(result) or {}).get("question") or {}).get("key")


@pytest.mark.parametrize("failure", [False, True])
async def test_blank_registration_reason_gets_complaint_then_all_sixteen_screens(
    failure: bool,
) -> None:
    extractor, urgency = ComplaintExtractor(fail=failure), StubUrgencyModel()
    graph = build_graph(
        stub_ports(
            conversation=ChecklistConversation(),
            extractor=extractor,
            urgency=urgency,
            rules=CareflowRules(),
        ),
        memory_checkpointer(),
    )
    initial = child_state()
    sid = initial["session_id"]
    result = await graph.ainvoke(initial, thread_config(sid))
    assert current_key(result) == "chief_complaint"
    result = await answer(graph, sid, "Fever since yesterday", "voice")
    for key in CHILD_BANK:
        assert current_key(result) == key.value
        result = await answer(graph, sid, "No", "tap")
    assert (pending(result) or {}).get("kind") == "nurse_review"
    assert result["turn_count"] == 17  # every bank answer survives the usual eight-turn cap
    chief = [entry for entry in result["facts"] if entry.key is FactKey.chief_complaint]
    assert len(chief) == 1 and chief[0].fact.value == "Fever since yesterday"
    assert extractor.calls == ["Fever since yesterday"]
    assert result.get("flags", {}).get("extraction_incomplete", False) is failure
    assert result["suggestion"] is None and urgency.calls == 0
    assert {entry.key for entry in result["facts"]} >= set(CHILD_BANK)


@pytest.mark.parametrize("urgent", [False, True])
async def test_existing_child_complaint_starts_bank_and_preserves_prior_urgent_answer(
    urgent: bool,
) -> None:
    extractor, urgency = ComplaintExtractor(), StubUrgencyModel()
    graph = build_graph(
        stub_ports(
            conversation=ChecklistConversation(),
            extractor=extractor,
            urgency=urgency,
            rules=CareflowRules(),
        ),
        memory_checkpointer(),
    )
    initial = child_state(existing_complaint=True, urgent=urgent)
    sid = initial["session_id"]
    result = await graph.ainvoke(initial, thread_config(sid))
    expected = CHILD_BANK[1:] if urgent else CHILD_BANK
    for key in expected:
        assert current_key(result) == key.value
        result = await answer(graph, sid, "No", "tap")
    assert (pending(result) or {}).get("kind") == "nurse_review"
    assert not extractor.calls and urgency.calls == 0
    assert result["turn_count"] == len(expected)
    assert {entry.key for entry in result["facts"]} >= set(CHILD_BANK)
    assert all(turn.question_key is not FactKey.chief_complaint for turn in result["turns"])
    if urgent:
        assert "NG143_RED_FLAG" in {alert.rule_id.value for alert in result["alerts"]}
        hard_to_wake = next(
            entry for entry in result["facts"] if entry.key is FactKey.child_danger_hard_to_wake
        )
        assert hard_to_wake.fact.value.value == "yes"
