"""LlmExtractor on recorded model replies (M1-06): verified spans, retry once, fail safe."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from careflow_contracts.generated.fact_schema import Provenance, TranscriptSpanSource
from careflow_contracts.generated.structured_facts_schema import FactKey, Turn
from careflow_intake.checkpoint import memory_checkpointer
from careflow_intake.extraction import PROMPT_VERSION, LlmExtractor
from careflow_intake.extraction.prompt import build_messages, extractable_keys
from careflow_intake.extraction.record import FIXTURES, ScriptedChat, load_case, recorded_provider
from careflow_intake.extraction.verify import find_span, number_in_quote
from careflow_intake.facts import FactEntry
from careflow_intake.graph import answer, build_graph, pending, start_intake
from careflow_intake.ports import ExtractionResult
from careflow_intake.stubs import START, RecordingRulesRunner, stable_fact_id
from careflow_providers.adapters.replay import ReplayProvider

CASES = sorted(path.stem for path in FIXTURES.glob("*.json"))
EXISTING = FactEntry.model_validate(
    {
        "key": "infection.fever_reported",
        "fact": {
            "fact_id": stable_fact_id("existing"),
            "provenance": "PATIENT_STATED",
            "source": {
                "kind": "transcript_span",
                "turn_id": "trn_000",
                "start": 0,
                "end": 3,
                "quote": "yes",
            },
            "recorded_at": "2026-09-27T08:59:00Z",
            "value": "yes",
        },
    }
)


def summary(entry: FactEntry) -> dict[str, Any]:
    source = entry.base.source
    assert isinstance(source, TranscriptSpanSource)
    return {
        "key": entry.key.value,
        "value": entry.base.model_dump(mode="json")["value"],
        "turn_id": source.turn_id,
        "quote": source.quote,
    }


def spans_slice(result: ExtractionResult, turns: list[Turn]) -> bool:
    """Every PATIENT_STATED fact's span slices, in its turn, to exactly its quote."""
    by_id = {turn.turn_id: turn for turn in turns}
    for entry in result.facts:
        if entry.provenance is not Provenance.PATIENT_STATED:
            return False
        source = entry.base.source
        if not isinstance(source, TranscriptSpanSource) or source.turn_id not in by_id:
            return False
        if by_id[source.turn_id].text[source.start : source.end] != source.quote:
            return False
    return True


async def run_case(name: str, tmp_path: Path) -> tuple[ExtractionResult, list[Turn]]:
    case = load_case(name)
    provider = recorded_provider(case, cassette=tmp_path / f"{name}.cassette.json")
    extractor = LlmExtractor(provider, provider_kind="scripted")
    return await extractor.extract(case.turns, [EXISTING], case.question_keys), case.turns


def test_the_required_cases_exist() -> None:
    assert {
        "chest_pain",
        "negation",
        "hallucinated_quote",
        "assistant_quote",
        "number_not_in_quote",
        "wrong_offsets",
        "retry_then_valid",
        "invalid_twice",
    } <= set(CASES)


@pytest.mark.parametrize("name", CASES)
async def test_extraction_case(name: str, tmp_path: Path) -> None:
    result, turns = await run_case(name, tmp_path)
    expected = load_case(name).expected
    assert result.extraction_incomplete is expected["extraction_incomplete"]
    assert result.error_code == expected.get("error_code")
    assert result.dropped_count == expected["dropped_count"]
    if expected["facts"] == "existing":  # fail safe: the known facts come back unchanged
        assert result.facts == [EXISTING]
    else:
        got = sorted((summary(entry) for entry in result.facts), key=lambda f: f["key"])
        assert got == sorted(expected["facts"], key=lambda f: f["key"])
    assert result.prompt_version == PROMPT_VERSION == "extraction_v2"
    assert result.provider == "scripted" and result.model_id == "scripted"


async def test_every_patient_stated_fact_has_a_span_that_slices_to_its_quote(
    tmp_path: Path,
) -> None:
    checked = 0
    for name in CASES:
        result, turns = await run_case(name, tmp_path)
        if not result.extraction_incomplete:
            assert spans_slice(result, turns), name
            checked += len(result.facts)
    assert checked >= 12  # the rate is over every fact returned by every case: 100%


async def test_wrong_offsets_are_recomputed(tmp_path: Path) -> None:
    result, turns = await run_case("wrong_offsets", tmp_path)
    by_key = {entry.key: entry.base.source for entry in result.facts}
    chest = by_key[FactKey.chest_pain_present]
    assert isinstance(chest, TranscriptSpanSource)
    text = turns[1].text  # "I've had chest pain since this morning."
    assert (
        (chest.start, chest.end)
        == (9, 19)
        == (text.find("chest pain"), text.find("chest pain") + 10)
    )
    assert text[chest.start : chest.end] == "chest pain"  # not the model's 0..3
    pain = by_key[FactKey.pain_score]
    assert isinstance(pain, TranscriptSpanSource) and pain.quote == "About a seven"


async def test_a_scripted_case_replays_from_its_cassette(tmp_path: Path) -> None:
    """The replies are recorded keyed by the exact messages (M0-06 ReplayProvider) and replay
    without any model."""
    case = load_case("chest_pain")
    cassette = tmp_path / "chest_pain.cassette.json"
    first = await LlmExtractor(recorded_provider(case, cassette=cassette)).extract(
        case.turns, [], case.question_keys
    )
    entries = json.loads(cassette.read_text(encoding="utf-8"))
    assert len(entries) == 1 and next(iter(entries.values()))["task"] == "extraction"
    replay = ReplayProvider(
        None, task="extraction", model="scripted", cassette=cassette, mode="replay"
    )
    second = await LlmExtractor(replay).extract(case.turns, [], case.question_keys)
    assert second.facts == first.facts and not second.extraction_incomplete


async def test_a_timeout_is_extraction_incomplete() -> None:
    case = load_case("chest_pain")
    slow = ScriptedChat(case.responses, delay_seconds=1.0)
    result = await LlmExtractor(slow, timeout_seconds=0.05).extract(
        case.turns, [EXISTING], case.question_keys
    )
    assert result.extraction_incomplete and result.error_code == "timeout"
    assert result.facts == [EXISTING]


async def test_a_provider_error_is_extraction_incomplete() -> None:
    case = load_case("chest_pain")
    result = await LlmExtractor(ScriptedChat([])).extract(case.turns, [], case.question_keys)
    assert result.extraction_incomplete and result.error_code == "provider_error"


async def test_switching_to_an_api_provider_needs_no_code_change(tmp_path: Path) -> None:
    """Only providers.yaml changes; the fake chat model stands in for the vendor (no key)."""
    config = tmp_path / "providers.yaml"
    config.write_text(
        "tasks:\n  extraction: { provider: api, vendor: openai, model: fake-extractor }\n",
        encoding="utf-8",
    )
    case = load_case("chest_pain")
    fake = GenericFakeChatModel(messages=iter([AIMessage(content=case.responses[0])]))
    extractor = LlmExtractor.from_config(config, chat_model=fake)
    result = await extractor.extract(case.turns, [], case.question_keys)
    assert result.provider == "api" and result.model_id == "fake-extractor"
    assert not result.extraction_incomplete and len(result.facts) == 5


async def test_a_failing_extractor_still_lets_run_rules_run(
    make_ports: Any, script: dict[str, Any]
) -> None:
    """The M1-05 stub graph with an LlmExtractor whose model never returns valid JSON."""
    rules = RecordingRulesRunner()
    extractor = LlmExtractor(ScriptedChat(["not json"] * 10))
    graph = build_graph(make_ports(extractor=extractor, rules=rules), memory_checkpointer())
    sid = script["session_id"]
    result = await start_intake(
        graph, session_id=sid, clinic_id=script["clinic_id"], arrived_at=START
    )
    for text in script["answers"]:
        result = await answer(graph, sid, text)
    assert (pending(result) or {})["kind"] == "nurse_review"
    assert result["flags"]["extraction_incomplete"] is True
    assert len(rules.seen) == 4 and all(
        f.extraction_status.value == "incomplete" for f in rules.seen
    )


def test_the_prompt_labels_turns_and_lists_only_patient_keys() -> None:
    case = load_case("chest_pain")
    system, user = build_messages(case.turns, case.question_keys)
    assert "chest_pain.present" in system["content"] and "vitals.spo2" not in system["content"]
    assert "Never infer a diagnosis" in system["content"]
    assert (
        "t2 [patient, answers chief_complaint]: I've had chest pain since this morning."
        in user["content"]
    )
    assert "t3 [assistant, asks about chest_pain.radiates]:" in user["content"]
    keys = {key.value for key in extractable_keys()}
    assert "pain_score" in keys and not {
        k for k in keys if k.startswith(("vitals.", "ehr_context."))
    }


@pytest.mark.parametrize(
    ("text", "quote", "span"),
    [
        ("About a seven.", "About a seven", (0, 13)),
        ("About a seven.", "about  a SEVEN", (0, 13)),
        ("Yes,  down my left arm.", "yes, down my left arm.", (0, 23)),
        ("No.", '"No."', (0, 2)),  # the quote's own punctuation is ignored: the span is "No"
        ("No chest pain.", "no fever", None),
    ],
)
def test_find_span(text: str, quote: str, span: tuple[int, int] | None) -> None:
    assert find_span(text, quote) == span


@pytest.mark.parametrize(
    ("number", "quote", "found"),
    [
        (7, "About a seven", True),
        (7, "7/10", True),
        (7, "17 out of 20", False),
        (38.5, "it was 38.5 at home", True),
        (38.5, "it was 38,5", True),
        (10, "ten out of ten", True),
        (9, "About a seven", False),
    ],
)
def test_number_in_quote(number: float, quote: str, found: bool) -> None:
    assert number_in_quote(number, quote) is found
