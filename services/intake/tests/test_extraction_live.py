"""The extractor on the real local model (live), and on its recorded replies (replay, in CI).

    CAREFLOW_LIVE_LLM=1 scripts/with_llm.sh uv run pytest services/intake -m live -s

Recording: scripts/with_llm.sh uv run python -m careflow_intake.extraction.record --case live/<name>
"""

from __future__ import annotations

import os
import statistics
import time
from typing import Any

import pytest

from careflow_contracts.generated.fact_schema import Provenance, TranscriptSpanSource
from careflow_intake.extraction import LlmExtractor
from careflow_intake.extraction.record import (
    Case,
    load_case,
    recorded_provider,
    warm_up,
)
from careflow_intake.ports import ExtractionResult
from careflow_intake.wiring import providers_config

LIVE_CASES = ("live/chest_pain", "live/fever", "live/ankle")


def check(case: Case, result: ExtractionResult) -> None:
    """Complete, every span verified, and the facts a correct extraction must contain."""
    assert not result.extraction_incomplete, (case.name, result.error_code)
    by_id = {turn.turn_id: turn for turn in case.turns}
    values: dict[str, Any] = {}
    for entry in result.facts:
        source = entry.base.source
        assert entry.provenance is Provenance.PATIENT_STATED
        assert isinstance(source, TranscriptSpanSource)
        assert by_id[source.turn_id].text[source.start : source.end] == source.quote
        values[entry.key.value] = entry.base.model_dump(mode="json")["value"]
    for key, value in case.expected["required"].items():
        assert values.get(key) == value, (case.name, key, values.get(key))
    for key in case.expected.get("present", []):
        assert key in values, (case.name, key)


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("CAREFLOW_LIVE_LLM") != "1",
    reason="needs CAREFLOW_LIVE_LLM=1 and mlx_lm.server (run through scripts/with_llm.sh)",
)
async def test_live_extraction_on_three_transcripts() -> None:
    extractor = LlmExtractor.from_config(providers_config())
    cold = await warm_up(extractor.provider)  # the first request after the server starts
    seconds = []
    for name in LIVE_CASES:
        case = load_case(name)
        start = time.perf_counter()
        result = await extractor.extract(case.turns, [], case.question_keys)
        seconds.append(time.perf_counter() - start)
        check(case, result)
    model = extractor.provider.model_name
    print(
        f"\nlive extraction ({model}): p50 {statistics.median(seconds):.2f} s, "
        f"max {max(seconds):.2f} s over {len(seconds)} transcripts (warm-up request {cold:.2f} s)"
    )
    print("  " + ", ".join(f"{n}: {s:.2f} s" for n, s in zip(LIVE_CASES, seconds, strict=True)))


@pytest.mark.parametrize("name", LIVE_CASES)
async def test_recorded_live_replies_replay(name: str) -> None:
    """The recorded replies of the real model, replayed without it (CI)."""
    case = load_case(name)
    if not case.cassette.exists():
        pytest.skip(f"{name} is not recorded (run record.py through scripts/with_llm.sh)")
    result = await LlmExtractor(recorded_provider(case)).extract(case.turns, [], case.question_keys)
    check(case, result)
