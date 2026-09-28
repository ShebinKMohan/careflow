"""Arabic extraction (M2-03): the Arabic prompt, spans into the original Arabic text, Arabic
numerals, the ten negation cases, unsure versus no, dropped facts and model failure.

    uv run pytest services/intake -k arabic

The negation, unsure and numeral cases replay the local model's recorded replies (Qwen3.5-9B,
recorded with `scripts/with_llm.sh uv run python -m careflow_intake.extraction.record --case
ar/<name>`); the other cases replay hand-written replies that test the verification code.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from careflow_contracts.generated.fact_schema import Provenance, TranscriptSpanSource
from careflow_contracts.generated.structured_facts_schema import Language, Turn
from careflow_intake.extraction import LlmExtractor
from careflow_intake.extraction.prompt import build_messages, prompt_version, template
from careflow_intake.extraction.record import (
    FIXTURES,
    Case,
    ScriptedChat,
    load_case,
    recorded_provider,
)
from careflow_intake.extraction.verify import find_span, number_in_quote
from careflow_intake.facts import FactEntry
from careflow_intake.ports import ExtractionResult
from careflow_intake.stubs import stable_fact_id
from careflow_providers.base import ChatResult, Message

AR = FIXTURES / "ar"
NAMES = sorted(
    f"ar/{path.stem}" for path in AR.glob("*.json") if not path.name.endswith(".cassette.json")
)
SCRIPTED = [name for name in NAMES if load_case(name).responses]
LIVE = [name for name in NAMES if name not in SCRIPTED]
NEGATIONS = [f"ar/negation_{number:02d}" for number in range(1, 11)]
EXISTING = FactEntry.model_validate(
    {
        "key": "infection.fever_reported",
        "fact": {
            "fact_id": stable_fact_id("existing-ar"),
            "provenance": "PATIENT_STATED",
            "source": {
                "kind": "transcript_span",
                "turn_id": "trn_000",
                "start": 0,
                "end": 3,
                "quote": "نعم",
            },
            "recorded_at": "2026-09-27T08:59:00Z",
            "value": "yes",
        },
    }
)


def values(result: ExtractionResult) -> dict[str, Any]:
    return {entry.key.value: entry.base.model_dump(mode="json")["value"] for entry in result.facts}


def slices(result: ExtractionResult, turns: list[Turn]) -> int:
    """How many facts were checked; fails unless every one is PATIENT_STATED and its span slices,
    in the original turn text, to exactly its quote."""
    by_id = {turn.turn_id: turn.text for turn in turns}
    for entry in result.facts:
        source = entry.base.source
        assert entry.provenance is Provenance.PATIENT_STATED
        assert isinstance(source, TranscriptSpanSource)
        assert by_id[source.turn_id][source.start : source.end] == source.quote
    return len(result.facts)


async def extract(case: Case, tmp_path: Path) -> ExtractionResult:
    provider = recorded_provider(case, cassette=tmp_path / "scripted.cassette.json")
    extractor = LlmExtractor(provider, provider_kind="replay")
    return await extractor.extract(case.turns, [EXISTING], case.question_keys, case.language)


def test_the_arabic_cases_exist_and_are_arabic() -> None:
    assert set(NEGATIONS) | {"ar/unsure_vs_no", "ar/numerals_digit", "ar/numerals_word"} <= set(
        LIVE
    )
    assert {
        "ar/offsets_normalised",
        "ar/hallucinated_quote",
        "ar/number_not_in_quote",
        "ar/invalid_twice",
    } <= set(SCRIPTED)
    for name in NAMES:
        assert load_case(name).language is Language.ar, name
    for name in LIVE:  # recorded from the model, replayed in CI
        assert load_case(name).cassette.is_file(), f"{name} is not recorded"


def test_the_arabic_prompt_is_selected_for_an_arabic_session() -> None:
    case = load_case("ar/negation_01")
    arabic = build_messages(case.turns, case.question_keys, Language.ar)[0]["content"]
    english = build_messages(case.turns, case.question_keys, Language.en)[0]["content"]
    assert prompt_version(Language.ar) == "extraction_v2_ar"
    assert arabic != english
    assert template("extraction_v2_ar").split("{keys}")[0] in arabic


async def test_an_arabic_result_names_the_arabic_prompt(tmp_path: Path) -> None:
    result = await extract(load_case("ar/offsets_normalised"), tmp_path)
    assert result.prompt_version == "extraction_v2_ar"


@pytest.mark.parametrize(
    ("text", "quote", "span"),
    [
        ("عِنْدِي أَلَمٌ فِي صَدْرِي", "عندي الم", "عِنْدِي أَلَمٌ"),  # diacritics
        ("صـــدري بيوجعني", "صدري", "صـــدري"),  # tatweel
        ("إيدي ضعيفة", "ايدي ضعيفة", "إيدي ضعيفة"),  # hamza below
        ("آلام في ظهري", "الام", "آلام"),  # madda
        ("آلام في ظهري", "آلام", "آلام"),  # a decomposed madda (NFC)
        ("أحس بضيقة", "احس بضيقه", None),  # ة and ه are different letters
        ("ما عندي حرارة", "عندي حمى", None),  # not said
    ],
)
def test_an_arabic_span_points_into_the_original_text(
    text: str, quote: str, span: str | None
) -> None:
    found = find_span(text, quote)
    if span is None:
        assert found is None
    else:
        assert found is not None and text[found[0] : found[1]] == span


@pytest.mark.parametrize(
    ("number", "quote", "found"),
    [
        (7, "الوجع ٧", True),  # Arabic-Indic digit
        (7, "الوجع ۷", True),  # Eastern Arabic-Indic digit
        (7.5, "حرارتي ٧٫٥", True),  # Arabic decimal separator
        (7, "سبعة", True),
        (8, "تقريباً تمانية", True),  # Levantine spelling
        (2, "تنين", True),
        (3, "وثلاثة", True),  # after و
        (10, "عشرة", True),
        (0, "صفر", True),
        (7, "ستة", False),
        (8, "الوجع ٧", False),
    ],
)
def test_arabic_numerals_are_found_in_their_quote(number: float, quote: str, found: bool) -> None:
    assert number_in_quote(number, quote) is found


@pytest.mark.parametrize("name", SCRIPTED)
async def test_an_arabic_scripted_case(name: str, tmp_path: Path) -> None:
    case = load_case(name)
    result = await extract(case, tmp_path)
    expected = case.expected
    assert result.extraction_incomplete is expected["extraction_incomplete"]
    assert result.error_code == expected.get("error_code")
    assert result.dropped_count == expected["dropped_count"]
    if expected["facts"] == "existing":  # fail safe: the known facts come back unchanged
        assert result.facts == [EXISTING]
        return
    got = []
    for entry in result.facts:
        if entry == EXISTING:
            continue
        source = entry.base.source
        assert isinstance(source, TranscriptSpanSource)
        got.append(
            {
                "key": entry.key.value,
                "value": entry.base.model_dump(mode="json")["value"],
                "turn_id": source.turn_id,
                "quote": source.quote,
            }
        )
        if "span" in expected:
            assert [source.start, source.end] == expected["span"]
    assert got == expected["facts"]


@pytest.mark.parametrize("name", NEGATIONS)
async def test_an_arabic_negation_is_no(name: str) -> None:
    case = load_case(name)
    result = await LlmExtractor(recorded_provider(case)).extract(
        case.turns, [], case.question_keys, case.language
    )
    assert not result.extraction_incomplete, result.error_code
    assert slices(result, case.turns) >= 1
    for key, value in case.expected["required"].items():
        assert values(result).get(key) == value, (name, values(result))


@pytest.mark.parametrize("name", ["ar/unsure_vs_no", "ar/numerals_digit", "ar/numerals_word"])
async def test_arabic_unsure_and_numerals_on_the_recorded_model(name: str) -> None:
    case = load_case(name)
    result = await LlmExtractor(recorded_provider(case)).extract(
        case.turns, [], case.question_keys, case.language
    )
    assert not result.extraction_incomplete, result.error_code
    slices(result, case.turns)
    got = values(result)
    for key, value in case.expected["required"].items():
        assert got.get(key) == value, (name, key, got)
    for key in case.expected.get("present", []):
        assert key in got, (name, key)


async def test_every_arabic_patient_stated_fact_slices_to_its_quote(tmp_path: Path) -> None:
    checked = 0
    for name in NAMES:
        case = load_case(name)
        result = await (
            extract(case, tmp_path / name.replace("/", "_"))
            if case.responses
            else LlmExtractor(recorded_provider(case)).extract(
                case.turns, [], case.question_keys, case.language
            )
        )
        if not result.extraction_incomplete:
            checked += slices(result, [*case.turns])
    assert checked >= 15  # every fact of every Arabic case: 100%


async def test_an_arabic_replay_is_deterministic() -> None:
    for name in LIVE:
        case = load_case(name)
        first = await LlmExtractor(recorded_provider(case)).extract(
            case.turns, [], case.question_keys, case.language
        )
        second = await LlmExtractor(recorded_provider(case)).extract(
            case.turns, [], case.question_keys, case.language
        )
        assert first == second, name


class Unreachable:
    task = "extraction"
    model_name = "unreachable"

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        raise ConnectionError("the local model is down")

    async def stream(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        raise ConnectionError("the local model is down")
        yield ""


async def test_an_arabic_model_failure_keeps_the_known_facts() -> None:
    case = load_case("ar/negation_01")
    down = await LlmExtractor(Unreachable()).extract(
        case.turns, [EXISTING], case.question_keys, case.language
    )
    assert down.extraction_incomplete and down.error_code == "provider_error"
    assert down.facts == [EXISTING] and down.prompt_version == "extraction_v2_ar"
    slow = ScriptedChat(["{}"], delay_seconds=1.0)
    late = await LlmExtractor(slow, timeout_seconds=0.05).extract(
        case.turns, [EXISTING], case.question_keys, case.language
    )
    assert late.extraction_incomplete and late.error_code == "timeout"
    assert late.facts == [EXISTING]


def test_the_arabic_fixtures_are_synthetic() -> None:
    for name in NAMES:
        body = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
        assert "Synthetic" in body["description"], name
