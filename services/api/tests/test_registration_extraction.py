"""Real registration facts stay sourced while only natural patient text reaches extraction."""

from __future__ import annotations

import json
import secrets
from collections.abc import Sequence
from typing import Any, cast

import pytest

from careflow_api.routers.reception import registration_facts
from careflow_contracts.api_models import AdaptiveCheckinRequest
from careflow_contracts.generated.structured_facts_schema import FactKey, Language, Turn, TurnRole
from careflow_intake.extraction.extractor import LlmExtractor
from careflow_intake.extraction.prompt import build_messages, registration_spans
from careflow_intake.extraction.record import ScriptedChat
from careflow_intake.facts import FactEntry, merge_facts_by_id
from careflow_intake.nodes.conversation import extract
from careflow_intake.state import IntakeSettings, IntakeState
from careflow_intake.stubs import START, stub_ports
from careflow_providers.base import ChatResult, Message


class CapturingChat(ScriptedChat):
    def __init__(self, facts: list[dict[str, Any]]) -> None:
        super().__init__([json.dumps({"facts": facts})])
        self.calls: list[Sequence[Message]] = []

    async def chat(
        self, messages: Sequence[Message], *, temperature: float = 0, max_tokens: int | None = None
    ) -> ChatResult:
        self.calls.append(messages)
        return await super().chat(messages, temperature=temperature, max_tokens=max_tokens)


def registered_state(reason: str = "", *, urgent: bool = False) -> IntakeState:
    body = AdaptiveCheckinRequest.model_validate(
        {
            "claim_key": secrets.token_urlsafe(32),
            "patient": {"name": "Synthetic Person", "age_years": 44, "sex": "male"},
            "visit_for": "self",
            "consent": {
                "camera": False,
                "voice": False,
                "data_use": True,
                "consent_version": "2026-09-29",
            },
            "reason": reason,
            "emergency_symptoms": ["chest_pain", "short_of_breath"] if urgent else [],
        }
    )
    turns, facts = registration_facts(body, START, "ses_registration_prompt")
    turns.extend(
        [
            Turn(
                turn_id="trn_question_002",
                role=TurnRole.assistant,
                text="What brings you in today?",
                question_key=FactKey.chief_complaint,
                created_at=START,
            ),
            Turn(
                turn_id="trn_answer_003",
                role=TurnRole.patient,
                text="My chest hurts since this morning.",
                question_key=FactKey.chief_complaint,
                created_at=START,
            ),
        ]
    )
    return {
        "session_id": "ses_registration_prompt",
        "clinic_id": "clinic_a",
        "arrived_at": START,
        "language": Language.en,
        "reception_context": {"intake_mode": "adaptive", "reason": reason},
        "turns": turns,
        "facts": facts,
        "last_answer_mode": "voice",
    }


@pytest.mark.parametrize("urgent", [False, True])
async def test_registration_boilerplate_never_reaches_model_and_typed_facts_survive(
    urgent: bool,
) -> None:
    current = registered_state(urgent=urgent)
    original_turns = [turn.model_dump(mode="json") for turn in current.get("turns", [])]
    original_facts = list(current.get("facts", []))
    chat = CapturingChat(
        [
            {
                "key": "chief_complaint",
                "value": "My chest hurts since this morning.",
                "turn_id": "t3",
                "quote": "My chest hurts since this morning.",
            }
        ]
    )
    result = await extract(
        current, stub_ports(extractor=LlmExtractor(chat, provider_kind="local")), IntakeSettings()
    )
    assert len(chat.calls) == 1
    prompt = chat.calls[0][1]["content"]
    assert "Age: 44" not in prompt and "Sex: male" not in prompt
    assert "Visit reason:" not in prompt and "Selected symptoms:" not in prompt
    assert "none selected" not in prompt
    assert "My chest hurts since this morning." in prompt
    assert "t2 [assistant" in prompt and "t3 [patient" in prompt
    assert "t1 [patient" not in prompt
    [complaint] = cast(list[FactEntry], result["facts"])
    source = complaint.base.source.model_dump()
    assert source["turn_id"] == "trn_answer_003"
    assert source["quote"] == current.get("turns", [])[-1].text[source["start"] : source["end"]]
    merged = merge_facts_by_id(original_facts, cast(list[FactEntry], result["facts"]))
    assert all(entry in merged for entry in original_facts)
    assert {entry.key for entry in merged} >= {FactKey.patient_age_years, FactKey.patient_sex}
    if urgent:
        assert {entry.key for entry in merged} >= {
            FactKey.chest_pain_present,
            FactKey.chest_pain_short_of_breath,
        }
    assert [turn.model_dump(mode="json") for turn in current.get("turns", [])] == original_turns
    assert current.get("facts") == original_facts


async def test_optional_registered_reason_keeps_original_quote_offsets_and_other_turns() -> None:
    current = registered_state("Dry cough since yesterday")
    original_turns = list(current.get("turns", []))
    original_facts = list(current.get("facts", []))
    chat = CapturingChat(
        [
            {"key": "symptoms", "value": "cough", "turn_id": "t1", "quote": "cough"},
            {
                "key": "symptom_onset_at",
                "value": "since this morning",
                "turn_id": "t3",
                "quote": "since this morning",
            },
        ]
    )
    result = await extract(
        current, stub_ports(extractor=LlmExtractor(chat, provider_kind="local")), IntakeSettings()
    )
    prompt = chat.calls[0][1]["content"]
    assert "Dry cough since yesterday" in prompt
    assert "My chest hurts since this morning." in prompt
    assert "Age: 44" not in prompt and "Sex: male" not in prompt and "none selected" not in prompt
    facts = cast(list[FactEntry], result["facts"])
    assert len(facts) == 2
    for entry in facts:
        source = entry.base.source.model_dump()
        original = next(turn for turn in original_turns if turn.turn_id == source["turn_id"])
        assert original.text[source["start"] : source["end"]] == source["quote"]
    symptom = next(entry for entry in facts if entry.key is FactKey.symptoms)
    assert symptom.base.source.model_dump()["start"] == original_turns[0].text.index("cough")
    merged = merge_facts_by_id(original_facts, facts)
    assert all(entry in merged for entry in original_facts)
    assert current.get("turns") == original_turns and current.get("facts") == original_facts


@pytest.mark.parametrize("has_reserved_id", [False, True])
def test_filter_requires_reserved_id_and_verified_typed_source_markers(
    has_reserved_id: bool,
) -> None:
    current = registered_state()
    if not has_reserved_id:
        # A genuine answer containing the exact boilerplate words is still natural input.
        current["turns"] = [
            turn.model_copy(update={"turn_id": "trn_genuine_answer"})
            if turn.turn_id == "trn_reception_001"
            else turn
            for turn in current.get("turns", [])
        ]
    else:
        current["facts"] = [
            entry for entry in current.get("facts", []) if entry.key is not FactKey.patient_sex
        ]
    turns, facts = current.get("turns", []), current.get("facts", [])
    assert registration_spans(turns, facts) == {}
    messages = build_messages(turns, {}, existing_facts=facts)
    assert all(turn.text in messages[1]["content"] for turn in turns)


async def test_model_cannot_quote_hidden_registration_metadata() -> None:
    current = registered_state()
    chat = CapturingChat(
        [{"key": "patient.age_years", "value": 44, "turn_id": "trn_reception_001", "quote": "44"}]
    )
    result = await extract(
        current, stub_ports(extractor=LlmExtractor(chat, provider_kind="local")), IntakeSettings()
    )
    assert result["facts"] == []
    assert cast(Any, result["extraction"])["dropped_count"] == 1
    assert any(entry.key is FactKey.patient_age_years for entry in current.get("facts", []))


async def test_padded_model_quotes_never_produce_nonoriginal_source_text() -> None:
    current = registered_state("Dry cough since yesterday")
    chat = CapturingChat(
        [
            {"key": "symptoms", "value": "cough", "turn_id": "t1", "quote": "     Dry cough"},
            {"key": "patient.age_years", "value": 44, "turn_id": "t1", "quote": "44"},
        ]
    )
    result = await extract(
        current, stub_ports(extractor=LlmExtractor(chat, provider_kind="local")), IntakeSettings()
    )
    facts = cast(list[FactEntry], result["facts"])
    # Existing normalization may recover the actual patient words; it never stores model padding.
    assert len(facts) == 1 and facts[0].key is FactKey.symptoms
    for entry in facts:
        source = entry.base.source.model_dump()
        original = next(
            turn for turn in current.get("turns", []) if turn.turn_id == source["turn_id"]
        )
        assert source["quote"] == original.text[source["start"] : source["end"]]
        assert not source["quote"].startswith("     ")
    assert cast(Any, result["extraction"])["dropped_count"] == 1
