"""Local presentation is smaller; canonical validation, full context and replay stay intact."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from careflow_contracts.generated.extraction_output_schema import ExtractionOutput
from careflow_contracts.generated.structured_facts_schema import Language, Turn, TurnRole
from careflow_intake.extraction.compact import OUTPUT_RULE, presentation_schema
from careflow_intake.extraction.extractor import LlmExtractor
from careflow_intake.extraction.prompt import build_messages, extractable_keys
from careflow_intake.extraction.record import ScriptedChat, load_case
from careflow_intake.stubs import START
from careflow_providers.adapters.replay import ReplayProvider
from careflow_providers.base import ChatResult, Message, schema_instruction
from careflow_providers.config import parse_config


class CapturingChat(ScriptedChat):
    def __init__(self, replies: Sequence[str]) -> None:
        super().__init__(replies)
        self.calls: list[Sequence[Message]] = []

    async def chat(
        self, messages: Sequence[Message], *, temperature: float = 0, max_tokens: int | None = None
    ) -> ChatResult:
        self.calls.append(messages)
        return await super().chat(messages, temperature=temperature, max_tokens=max_tokens)


def test_presentation_removes_only_annotations_unused_fields_and_disallowed_keys() -> None:
    canonical = ExtractionOutput.model_json_schema()
    compact = presentation_schema()
    assert len(json.dumps(compact)) < len(json.dumps(canonical)) * 0.6
    shown = compact["$defs"]["ExtractedFact"]
    assert set(shown["properties"]) == {"key", "value", "turn_id", "quote", "negated"}
    assert set(shown["required"]) == {"key", "value", "turn_id", "quote"}
    assert shown["additionalProperties"] is False
    assert shown["properties"]["quote"]["maxLength"] == 1000
    assert shown["properties"]["negated"] == {"type": "boolean", "const": True}
    assert set(compact["$defs"]["FactKey"]["enum"]) == {k.value for k in extractable_keys()}
    assert "char_start" in canonical["$defs"]["ExtractedFact"]["properties"]


@pytest.mark.parametrize("language", [Language.en, Language.ar])
async def test_local_keeps_full_transcript_and_semantic_rules(language: Language) -> None:
    case = load_case("chest_pain")
    chat = CapturingChat(['{"facts":[]}'])
    result = await LlmExtractor(chat, provider_kind="local", experimental_compact=True).extract(
        case.turns, [], case.question_keys, language
    )
    original = build_messages(case.turns, case.question_keys, language)
    assert chat.calls[0][1] == original[1]
    assert original[0]["content"].replace(', "negated": false', "") in chat.calls[0][0]["content"]
    assert OUTPUT_RULE in chat.calls[0][0]["content"]
    assert result.prompt_version is not None and result.prompt_version.endswith("_compact1")


@pytest.mark.parametrize("kind", ["api", "cli", "replay"])
async def test_other_provider_request_identity_is_unchanged(kind: str) -> None:
    case = load_case("chest_pain")
    chat = CapturingChat(['{"facts":[]}'])
    result = await LlmExtractor(chat, provider_kind=kind, experimental_compact=True).extract(
        case.turns, [], case.question_keys
    )
    original = build_messages(case.turns, case.question_keys)
    assert chat.calls[0][0]["content"] == (
        original[0]["content"] + "\n\n" + schema_instruction(ExtractionOutput)
    )
    assert result.prompt_version == "extraction_v2"


async def test_local_recording_and_replay_keep_identical_existing_requests(tmp_path: Path) -> None:
    case = load_case("chest_pain")
    cassette = tmp_path / "compact-isolation.json"
    recording = ReplayProvider(
        ScriptedChat(['{"facts":[]}']),
        task="extraction",
        model="scripted",
        cassette=cassette,
        mode="record",
    )
    first = await LlmExtractor(recording, provider_kind="local", experimental_compact=True).extract(
        case.turns, [], case.question_keys
    )
    replay = ReplayProvider(
        None, task="extraction", model="scripted", cassette=cassette, mode="replay"
    )
    again = await LlmExtractor(replay, provider_kind="replay", experimental_compact=True).extract(
        case.turns, [], case.question_keys
    )
    assert first.prompt_version == again.prompt_version == "extraction_v2"
    assert not first.extraction_incomplete and not again.extraction_incomplete


@pytest.mark.parametrize(
    ("candidate", "error"),
    [
        ({"key": "invented", "value": True}, "invalid_output"),
        ({"key": "chest_pain.present", "value": []}, "invalid_output"),
        ({"key": "pain_score", "value": 9, "quote": "seven"}, None),
        (
            {"key": "chest_pain.present", "value": True, "turn_id": "t1", "quote": "chest pain"},
            None,
        ),
        ({"key": "chest_pain.present", "value": True, "negated": True}, None),
        ({"key": "chest_pain.present", "value": True, "quote": "invented words"}, None),
        ({"key": "vitals.spo2", "value": 98}, None),
    ],
)
async def test_local_invalid_candidates_keep_canonical_rejection(
    candidate: dict[str, Any],
    error: str | None,
) -> None:
    turns = [
        Turn(
            turn_id="trn_001",
            role=TurnRole.assistant,
            text="Do you have chest pain?",
            created_at=START,
        ),
        Turn(
            turn_id="trn_002",
            role=TurnRole.patient,
            text="No chest pain. Pain is seven.",
            created_at=START,
        ),
    ]
    fact = {"turn_id": "t2", "quote": "No chest pain", **candidate}
    output = json.dumps({"facts": [fact]})
    local = await LlmExtractor(
        CapturingChat([output, output]), provider_kind="local", experimental_compact=True
    ).extract(turns, [], {})
    standard = await LlmExtractor(
        CapturingChat([output, output]), provider_kind="scripted"
    ).extract(turns, [], {})
    assert local.facts == standard.facts == []
    assert local.error_code == standard.error_code == error
    assert local.dropped_count == standard.dropped_count


async def test_omitted_defaults_and_explicit_denial_both_validate() -> None:
    turn = Turn(
        turn_id="trn_001", role=TurnRole.patient, text="Fever, but no chest pain.", created_at=START
    )
    output = json.dumps(
        {
            "facts": [
                {
                    "key": "infection.fever_reported",
                    "value": True,
                    "turn_id": "t1",
                    "quote": "Fever",
                },
                {
                    "key": "chest_pain.present",
                    "value": False,
                    "turn_id": "t1",
                    "quote": "no chest pain",
                    "negated": True,
                },
            ]
        }
    )
    result = await LlmExtractor(
        CapturingChat([output]), provider_kind="local", experimental_compact=True
    ).extract([turn], [], {})
    assert {entry.key.value: entry.fact.model_dump()["value"] for entry in result.facts} == {
        "infection.fever_reported": "yes",
        "chest_pain.present": "no",
    }
    assert not result.extraction_incomplete and result.dropped_count == 0


@pytest.mark.parametrize("language", [Language.en, Language.ar])
async def test_local_default_uses_exact_canonical_request(language: Language) -> None:
    case = load_case("chest_pain")
    chat = CapturingChat(['{"facts":[]}'])
    result = await LlmExtractor(chat, provider_kind="local").extract(
        case.turns, [], case.question_keys, language
    )
    original = build_messages(case.turns, case.question_keys, language)
    assert chat.calls[0][0]["content"] == (
        original[0]["content"] + "\n\n" + schema_instruction(ExtractionOutput)
    )
    assert not result.prompt_version or not result.prompt_version.endswith("_compact1")


@pytest.mark.parametrize("flag", [None, "", "0", "true", "1"])
def test_config_requires_explicit_experimental_flag(
    monkeypatch: pytest.MonkeyPatch, flag: str | None
) -> None:
    key = "CAREFLOW_EXPERIMENTAL_COMPACT_EXTRACTION"
    if flag is None:
        monkeypatch.delenv(key, raising=False)
    else:
        monkeypatch.setenv(key, flag)
    provider = CapturingChat([])
    monkeypatch.setattr(
        "careflow_intake.extraction.extractor.build_provider", lambda *args, **kwargs: provider
    )
    config = parse_config({"tasks": {"extraction": {"provider": "local", "model": "test-local"}}})
    assert LlmExtractor.from_config(config).compact_local is (flag == "1")
