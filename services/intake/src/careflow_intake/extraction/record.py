"""Record the model's extraction output for a fixture transcript, and replay recorded outputs.

    scripts/with_llm.sh uv run python -m careflow_intake.extraction.record --case live/chest_pain

Recording runs the configured `extraction` provider (the local model) inside the M0-06
`ReplayProvider` in record mode. The raw replies land in `<case>.cassette.json` next to the
fixture, keyed by the exact messages sent. Tests replay the cassette without a model. A changed
prompt changes the key, the replay misses, and the case must be recorded again.

A fixture holds `turns`, optional scripted `responses` (hand-written model replies, for the
failure cases), the `expected` result and its session `language` ("en" by default; the Arabic
fixtures in `ar/` say "ar", so the Arabic prompt is used, M2-03).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from careflow_contracts.generated.extraction_output_schema import ExtractionOutput
from careflow_contracts.generated.structured_facts_schema import FactKey, Language, Turn, TurnRole
from careflow_intake.extraction.extractor import DEFAULT_CONFIG, TASK, LlmExtractor
from careflow_intake.extraction.prompt import build_messages
from careflow_intake.ports import ExtractionResult
from careflow_intake.wiring import providers_config
from careflow_providers.adapters.replay import ReplayProvider
from careflow_providers.base import ChatProvider, ChatResult, Message, schema_instruction
from careflow_providers.factory import build_provider

FIXTURES: Final = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "extraction"
SCRIPTED_MODEL: Final = "scripted"


@dataclass(frozen=True)
class Case:
    name: str
    path: Path
    turns: list[Turn]
    responses: list[str]
    expected: dict[str, Any]
    language: Language = Language.en

    @property
    def question_keys(self) -> dict[str, FactKey]:
        """Patient turn id -> the fact its question asked about (as the intake graph passes it)."""
        return {
            turn.turn_id: turn.question_key
            for turn in self.turns
            if turn.role is TurnRole.patient and turn.question_key is not None
        }

    @property
    def cassette(self) -> Path:
        return self.path.with_suffix(".cassette.json")


def load_case(name: str, fixtures: Path = FIXTURES) -> Case:
    path = fixtures / f"{name}.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    return Case(
        name=name,
        path=path,
        turns=[Turn.model_validate(turn) for turn in body["turns"]],
        responses=list(body.get("responses", [])),
        expected=dict(body.get("expected", {})),
        language=Language(body.get("language", "en")),
    )


class ScriptedChat:
    """A ChatProvider that replies with hand-written texts in order (optionally slowly)."""

    def __init__(self, replies: Sequence[str], *, delay_seconds: float = 0.0) -> None:
        self.task = TASK
        self.model_name = SCRIPTED_MODEL
        self._replies = list(replies)
        self._delay = delay_seconds

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        if self._delay:
            await asyncio.sleep(self._delay)
        if not self._replies:
            raise RuntimeError("the scripted provider has no reply left")
        return ChatResult(text=self._replies.pop(0), model=self.model_name)

    async def stream(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        yield (await self.chat(messages)).text


def recorded_provider(case: Case, *, cassette: Path | None = None) -> ReplayProvider:
    """The M0-06 ReplayProvider for a case. A case recorded from a model replays its cassette.
    A scripted case records its hand-written replies into `cassette` as they are served, so later
    runs can replay them too."""
    if case.cassette.exists() and not case.responses:
        entries = json.loads(case.cassette.read_text(encoding="utf-8"))
        model = next(iter(entries.values()))["model"] if entries else SCRIPTED_MODEL
        return ReplayProvider(None, task=TASK, model=model, cassette=case.cassette, mode="replay")
    if cassette is None:
        raise ValueError(f"{case.name}: a scripted case needs a cassette path to record into")
    inner = ScriptedChat(case.responses)
    return ReplayProvider(inner, task=TASK, model=SCRIPTED_MODEL, cassette=cassette, mode="record")


async def warm_up(provider: ChatProvider) -> float:
    """One untimed request that starts with the extraction system prompt (and its schema), so a
    freshly started server has loaded the model and cached that prefix before timed calls. The
    first extraction after start-up otherwise takes about twice as long. Returns its duration."""
    system = build_messages([], {})[0]["content"]
    messages: list[Message] = [
        {"role": "system", "content": f"{system}\n\n{schema_instruction(ExtractionOutput)}"},
        {"role": "user", "content": "Conversation:\nt1 [patient]: hello"},
    ]
    start = time.perf_counter()
    await provider.chat(messages, max_tokens=1)
    return time.perf_counter() - start


async def record(name: str, *, config: Path = DEFAULT_CONFIG) -> tuple[ExtractionResult, float]:
    """Run the configured model on the case and save its replies to the case's cassette."""
    case = load_case(name)
    providers = providers_config(config)
    inner = build_provider(TASK, providers)
    await warm_up(inner)
    provider = ReplayProvider(
        inner, task=TASK, model=inner.model_name, cassette=case.cassette, mode="record"
    )
    extractor = LlmExtractor(provider, provider_kind=providers.tasks[TASK].provider)
    start = time.perf_counter()
    result = await extractor.extract(case.turns, [], case.question_keys, case.language)
    return result, time.perf_counter() - start


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--case", required=True, help="fixture name, e.g. live/chest_pain")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)
    result, seconds = asyncio.run(record(args.case, config=args.config))
    print(
        f"{args.case}: {len(result.facts)} facts, {result.dropped_count} dropped, "
        f"incomplete={result.extraction_incomplete} ({result.error_code}), {seconds:.1f} s"
    )
    for entry in result.facts:
        print(f"  {entry.key.value} = {entry.base.model_dump(mode='json')['value']!r}")
    return 1 if result.extraction_incomplete else 0


if __name__ == "__main__":
    sys.exit(main())
