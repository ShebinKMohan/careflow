"""`LlmExtractor`: the M1-05 `Extractor` port over the provider layer's `extraction` task.

One call per patient answer over the whole transcript: the provider layer validates the reply
against `ExtractionOutput` and retries once with the errors; code then verifies every quote and
number. A second invalid reply, a timeout or a provider error returns the facts already known with
`extraction_incomplete`, so the rules still run and the nurse sees it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final, Literal

from langchain_core.language_models import BaseChatModel
from pydantic import ValidationError

from careflow_contracts.generated.extraction_output_schema import ExtractionOutput
from careflow_contracts.generated.fact_schema import (
    IntegerFact,
    Provenance,
    TextFact,
    TriState,
    TriStateFact,
)
from careflow_contracts.generated.structured_facts_schema import FactKey, Language, Symptom, Turn
from careflow_intake import arabic
from careflow_intake.extraction.prompt import build_messages, extractable_keys, prompt_version
from careflow_intake.extraction.verify import VerifiedFact, numeric_value, verify_facts
from careflow_intake.facts import LIST_KEYS, FactEntry, fact_class, stable_fact_id
from careflow_intake.ports import ExtractionResult
from careflow_providers.adapters.replay import ReplayProvider
from careflow_providers.base import ChatProvider, StructuredFailure, structured
from careflow_providers.config import ProvidersConfig, load_config
from careflow_providers.factory import build_provider

logger = logging.getLogger("careflow_intake.extraction")

TASK: Final = "extraction"
DEFAULT_TIMEOUT_SECONDS: Final = 20.0
DEFAULT_CONFIG: Final = Path(__file__).resolve().parents[5] / "config" / "providers.yaml"
YES: Final = frozenset({"yes", "y", "true"})
NO: Final = frozenset({"no", "n", "false"})
UNSURE: Final = frozenset({"unknown", "unsure", "not sure", "don't know"})

type ErrorCode = Literal["timeout", "invalid_output", "provider_error"]


def _tri_state(value: bool | int | float | str, negated: bool) -> str | None:
    """yes/no/unknown, or None when the value is not a yes/no answer or contradicts `negated`.
    An English or Arabic yes/no word counts, though the prompts ask for true or false."""
    word = value if isinstance(value, bool) else arabic.normalise(str(value).strip())
    if word is True or word in YES or word in arabic.YES:
        return None if negated else TriState.yes.value
    if word is False or word in NO or word in arabic.NO:
        return TriState.no.value
    if word in UNSURE or word in arabic.UNSURE:
        return TriState.unknown.value
    return None


def _fact_value(key: FactKey, verified: VerifiedFact) -> Any:
    """The contract value for `key`, or None when the model's value does not fit it."""
    cls, value = fact_class(key), verified.fact.value
    if issubclass(cls, TriStateFact):
        return _tri_state(value, verified.fact.negated)
    if issubclass(cls, IntegerFact):
        number = numeric_value(value)
        return int(number) if number is not None and number.is_integer() else None
    if issubclass(cls, TextFact | Symptom):
        if isinstance(value, str) and value.strip():
            return value.strip()
        # A yes/no given for a free-text answer ("No, none that I know of" to the allergy
        # question): keep the patient's own verified words.
        return verified.quote.strip() or None
    return None


def _entry(verified: VerifiedFact) -> FactEntry | None:
    """A PATIENT_STATED fact citing the verified span, or None when the value does not fit."""
    key, turn = verified.fact.key, verified.turn
    value = _fact_value(key, verified)
    if value is None or len(verified.quote) > 1000:
        return None
    parts = [turn.turn_id, turn.created_at.isoformat(), key.value]
    if key in LIST_KEYS:  # several symptoms can come from one turn
        parts.append(str(verified.start))
    fact: dict[str, Any] = {
        "fact_id": stable_fact_id(*parts),
        "provenance": Provenance.PATIENT_STATED,
        "source": {
            "kind": "transcript_span",
            "turn_id": turn.turn_id,
            "start": verified.start,
            "end": verified.end,
            "quote": verified.quote,
        },
        "recorded_at": turn.created_at,
    }
    if fact_class(key) is Symptom:
        body: dict[str, Any] = {"name": {**fact, "value": value}}
    else:
        body = {**fact, "value": value}
    try:
        return FactEntry.model_validate({"key": key, "fact": body})
    except ValidationError:  # e.g. a pain score of 11
        return None


class LlmExtractor:
    """The `Extractor` port. `provider` is any M0-06 `ChatProvider` for the `extraction` task."""

    def __init__(
        self,
        provider: ChatProvider,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        provider_kind: str | None = None,
    ) -> None:
        self.provider = provider
        self.timeout_seconds = timeout_seconds
        self.provider_kind = (
            provider_kind or type(provider).__name__.removesuffix("Provider").lower()
        )

    @classmethod
    def from_config(
        cls,
        config: ProvidersConfig | str | Path = DEFAULT_CONFIG,
        *,
        chat_model: BaseChatModel | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> LlmExtractor:
        """The extractor for whatever `config/providers.yaml` sets for `extraction`: switching
        providers is a config change only."""
        providers = config if isinstance(config, ProvidersConfig) else load_config(config)
        provider = build_provider(TASK, providers, chat_model=chat_model)
        # A replayed provider says so: its facts come from a recording, not from the model.
        replaying = isinstance(provider, ReplayProvider) and provider.mode == "replay"
        kind = "replay" if replaying else providers.tasks[TASK].provider
        return cls(provider, timeout_seconds=timeout_seconds, provider_kind=kind)

    def _result(self, language: Language, **fields: Any) -> ExtractionResult:
        return ExtractionResult(
            prompt_version=prompt_version(language),
            model_id=self.provider.model_name,
            provider=self.provider_kind,
            **fields,
        )

    def _incomplete(
        self, language: Language, existing: Sequence[FactEntry], code: ErrorCode
    ) -> ExtractionResult:
        return self._result(
            language, facts=list(existing), extraction_incomplete=True, error_code=code
        )

    async def extract(
        self,
        turns: Sequence[Turn],
        existing_facts: Sequence[FactEntry],
        question_keys: Mapping[str, FactKey],
        language: Language = Language.en,
    ) -> ExtractionResult:
        """PATIENT_STATED facts from the transcript, with the session language's prompt."""
        language = Language(language)
        transcript = list(turns)
        messages = build_messages(transcript, question_keys, language)
        try:
            outcome = await asyncio.wait_for(
                structured(self.provider, messages, ExtractionOutput, temperature=0.0),
                timeout=self.timeout_seconds,
            )
        except TimeoutError:
            logger.warning("extraction.timeout after %.1f s", self.timeout_seconds)
            return self._incomplete(language, existing_facts, "timeout")
        except Exception as exc:  # fail safe: provider down, no key, cassette miss
            logger.warning("extraction.provider_error: %s", type(exc).__name__)
            return self._incomplete(language, existing_facts, "provider_error")
        if isinstance(outcome, StructuredFailure):
            logger.warning("extraction.invalid_output after %d errors", len(outcome.errors))
            return self._incomplete(language, existing_facts, "invalid_output")

        allowed = set(extractable_keys())
        usable = [fact for fact in outcome.value.facts if fact.key in allowed]
        for fact in outcome.value.facts:
            if fact.key not in allowed:
                logger.info(
                    "extraction.fact_dropped",
                    extra={"key": fact.key.value, "reason": "key_not_extractable"},
                )
        verified, dropped = verify_facts(usable, transcript)
        position = {turn.turn_id: index for index, turn in enumerate(transcript)}
        latest: dict[FactKey, FactEntry] = {}
        listed: list[FactEntry] = []
        unusable = len(outcome.value.facts) - len(usable) + len(dropped)
        # Transcript order, so for the same key the latest patient statement wins.
        for item in sorted(verified, key=lambda v: position[v.turn.turn_id]):
            entry = _entry(item)
            if entry is None:
                unusable += 1
                logger.info(
                    "extraction.fact_dropped",
                    extra={"key": item.fact.key.value, "reason": "value_does_not_fit"},
                )
            elif entry.key in LIST_KEYS:
                listed.append(entry)
            else:
                latest[entry.key] = entry
        return self._result(language, facts=[*listed, *latest.values()], dropped_count=unusable)


__all__ = ["DEFAULT_CONFIG", "TASK", "LlmExtractor"]
