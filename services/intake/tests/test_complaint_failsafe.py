"""Model failures retain the patient's complaint verbatim without treating questions as facts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

import pytest

from careflow_contracts.generated.structured_facts_schema import FactKey, Language, Turn, TurnRole
from careflow_contracts.generated.turn_intent_schema import TurnIntent
from careflow_intake.checkpoint import memory_checkpointer
from careflow_intake.conversation import ChecklistConversation
from careflow_intake.facts import FactEntry
from careflow_intake.graph import answer, build_graph, pending, start_intake
from careflow_intake.nodes.conversation import extract
from careflow_intake.ports import ExtractionResult
from careflow_intake.state import IntakeSettings, IntakeState
from careflow_intake.stubs import START, stub_ports
from careflow_intake.tap import answer_fact


@dataclass
class FailingExtractor:
    mode: str = "incomplete"
    returned: FactEntry | None = None

    async def extract(
        self,
        turns: Sequence[Turn],
        existing_facts: Sequence[FactEntry],
        question_keys: Mapping[str, FactKey],
        language: Language = Language.en,
    ) -> ExtractionResult:
        if self.mode == "raises":
            raise TimeoutError("synthetic extraction timeout")
        return ExtractionResult(
            facts=[self.returned] if self.returned else [],
            extraction_incomplete=self.mode == "incomplete",
            provider="test",
        )


def state(text: str, key: FactKey = FactKey.chief_complaint) -> IntakeState:
    return {
        "session_id": "ses_complaint_failsafe",
        "clinic_id": "clinic_a",
        "arrived_at": START,
        "language": Language.en,
        "turns": [
            Turn(
                turn_id="trn_answer_001",
                role=TurnRole.patient,
                text=text,
                question_key=key,
                created_at=START,
            )
        ],
        "facts": [],
        "last_answer_mode": "voice",
    }


@pytest.mark.parametrize("failure", ["raises", "incomplete"])
@pytest.mark.parametrize("input_mode", ["voice", "text"])
async def test_failed_chief_answer_remains_sourced_and_is_not_asked_again(
    failure: str, input_mode: str
) -> None:
    ports = stub_ports(conversation=ChecklistConversation(), extractor=FailingExtractor(failure))
    graph = build_graph(ports, memory_checkpointer())
    sid, said = "ses_complaint_failsafe", "My chest has been aching since this morning."
    first = await start_intake(graph, session_id=sid, clinic_id="clinic_a", arrived_at=START)
    assert (pending(first) or {})["question"]["key"] == "chief_complaint"
    result = await answer(graph, sid, said, input_mode)
    [entry] = [f for f in result["facts"] if f.key is FactKey.chief_complaint]
    assert entry.fact.value == said
    assert entry.provenance.value == "PATIENT_STATED"
    source = entry.fact.source
    turn = next(turn for turn in result["turns"] if turn.turn_id == source.turn_id)
    assert source.quote == turn.text[source.start : source.end] == said
    assert result["flags"]["extraction_incomplete"] is True
    assert (pending(result) or {})["question"]["key"] != "chief_complaint"
    assert not any(f.key is FactKey.chest_pain_present for f in result["facts"])


@pytest.mark.parametrize("failure", ["raises", "incomplete"])
@pytest.mark.parametrize("side_marker", ["turn_id", "intent"])
async def test_side_questions_never_become_a_fallback_complaint(
    failure: str, side_marker: str
) -> None:
    current = state("Where is the pharmacy?")
    if side_marker == "turn_id":
        current["side_turn_ids"] = [current.get("turns", [])[0].turn_id]
    else:
        current["turn_intent"] = TurnIntent(
            intake_answer=False, clinic_question=True, medical_question=False
        )
    result = await extract(
        current, stub_ports(extractor=FailingExtractor(failure)), IntakeSettings()
    )
    assert result.get("facts", []) == []
    assert cast(Any, result["flags"])["extraction_incomplete"] is True


@pytest.mark.parametrize("already_known", [True, False])
async def test_fallback_does_not_replace_existing_or_extracted_complaint(
    already_known: bool,
) -> None:
    current = state("The pain is still there.")
    original = answer_fact(current.get("turns", [])[0], FactKey.chief_complaint, "Pain")
    assert original is not None
    if already_known:
        current["facts"] = [original]
    result = await extract(
        current,
        stub_ports(extractor=FailingExtractor(returned=None if already_known else original)),
        IntakeSettings(),
    )
    assert result.get("facts") == ([] if already_known else [original])
    assert cast(Any, result["flags"])["extraction_incomplete"] is True


async def test_successful_empty_extraction_or_other_question_has_no_complaint_fallback() -> None:
    for current, extractor in (
        (state("Nothing else"), FailingExtractor("complete")),
        (state("Since Monday", FactKey.symptom_onset_at), FailingExtractor()),
    ):
        result = await extract(current, stub_ports(extractor=extractor), IntakeSettings())
        assert result.get("facts") == []


async def test_long_complaint_uses_exact_bounded_source_and_retains_full_transcript() -> None:
    said = "The patient's exact description. " * 75
    current = state(said)
    result = await extract(current, stub_ports(extractor=FailingExtractor()), IntakeSettings())
    [entry] = cast(list[FactEntry], result["facts"])
    fact = cast(Any, entry.fact)
    assert fact.value == said[:1000]
    assert fact.source.end == 1000
    assert fact.source.quote == said[:1000]
    assert current.get("turns", [])[0].text == said
    assert cast(Any, result["flags"])["extraction_incomplete"] is True
