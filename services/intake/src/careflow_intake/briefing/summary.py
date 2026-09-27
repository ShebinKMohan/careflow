"""The optional briefing summary (M1-16): a model may only rephrase the items it cites.

`Summarizer` asks the `summary` provider (config/providers.yaml) for at most five sentences over
the briefing's fact items, each citing fact ids. `check_summary` then drops the whole summary
when any sentence:

- cites no fact, or a fact that is not an item of this briefing;
- contains a number that is not in the items it cites;
- reads as a diagnosis or advice: the patient-facing output filter's patterns, plus hedges such
  as "likely", "suggests", "consistent with" and "rule out" (safety rule 2). A condition name or
  a dose is allowed only when the cited items contain it.

The draft schema is lenient on purpose, so that the check (not the JSON validation) rejects a
bad draft and logs why. A provider that is missing or unreachable disables the summary. Either
way the plain list is complete.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field

from careflow_contracts.api_models import (
    Briefing,
    BriefingItem,
    BriefingSentence,
    BriefingSummary,
    SummaryStatus,
)
from careflow_intake.briefing.builder import TAGS
from careflow_intake.conversation.output_filter import ADVICE, CONDITIONS, DIAGNOSIS, DOSING
from careflow_providers.base import ChatProvider, Message, StructuredFailure, structured

logger = logging.getLogger(__name__)

TASK: Final = "summary"
PROMPT_VERSION: Final = "briefing-summary-v1"
MAX_SENTENCES: Final = 5
MAX_SENTENCE_CHARS: Final = 400
DEFAULT_TIMEOUT_SECONDS: Final = 60.0
#: Room for five sentences and their fact ids (mlx_lm.server stops at 512 tokens by default).
MAX_TOKENS: Final = 1536
NUMBER: Final = re.compile(r"\d+(?:[.,]\d+)?")
HEDGES: Final = re.compile(
    r"\b(?:likely|unlikely|probabl\w*|possibl\w*|suggest\w*|consistent\s+with|"
    r"rules?\s+out|ruled\s+out|should|recommend\w*|advis\w*|consider\w*|diagnos\w*)\b",
    re.IGNORECASE,
)
SYSTEM_PROMPT: Final = (
    "You summarise a doctor's triage briefing. Research prototype; synthetic data only.\n"
    f"Write at most {MAX_SENTENCES} short sentences in plain English for a doctor.\n"
    "Only rephrase the items below. Add nothing that is not in them: no diagnosis, no likely "
    "cause, no advice, no treatment and no doses.\n"
    "Keep every number exactly as it is in the items, and add no other number.\n"
    "Each sentence lists, in fact_ids, the ids of the items it rephrases.\n"
    "Write the JSON on one line, without indentation."
)


class DraftSentence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str
    fact_ids: list[str] = []


class SummaryDraft(BaseModel):
    """What the model is asked for. Lenient except for the sentence count, so the model is
    asked again when it writes too many; `check_summary` does the rest of the rejecting (and
    checks the count again)."""

    model_config = ConfigDict(extra="ignore")

    sentences: Annotated[list[DraftSentence], Field(max_length=MAX_SENTENCES)]


@dataclass(frozen=True)
class SummaryOutcome:
    status: SummaryStatus
    summary: BriefingSummary | None = None
    reasons: list[str] = field(default_factory=list)
    #: The prompt version, when a request reached the model.
    prompt_version: str | None = None


def fact_items(briefing: Briefing) -> list[BriefingItem]:
    """The items a summary may cite: facts whose source passed the check."""
    return [
        item
        for section in briefing.sections
        for item in section.items
        if item.fact_id is not None and not item.source_missing
    ]


def build_messages(items: Sequence[BriefingItem]) -> list[Message]:
    lines = [f"{item.fact_id} {TAGS[item.provenance]} {item.text}" for item in items]
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "Items:\n" + "\n".join(lines)},
    ]


def numbers(text: str) -> set[Decimal]:
    return {Decimal(found.replace(",", ".")).normalize() for found in NUMBER.findall(text)}


def sentence_problem(sentence: DraftSentence, items: dict[str, BriefingItem]) -> str | None:
    if not sentence.text.strip():
        return "is empty"
    if len(sentence.text) > MAX_SENTENCE_CHARS:
        return f"is longer than {MAX_SENTENCE_CHARS} characters"
    if not sentence.fact_ids:
        return "cites no fact"
    unknown = [fact_id for fact_id in sentence.fact_ids if fact_id not in items]
    if unknown:
        return f"cites a fact that is not in the briefing ({unknown[0]})"
    cited = " ".join(items[fact_id].text for fact_id in sentence.fact_ids)
    extra = numbers(sentence.text) - numbers(cited)
    if extra:
        return f"has a number that is not in its cited facts ({min(extra)})"
    lowered = cited.lower()
    if any(pattern.search(sentence.text) for pattern in (*DIAGNOSIS, *ADVICE)):
        return "reads as a diagnosis or advice"
    if (found := HEDGES.search(sentence.text)) is not None:
        return f"reads as a diagnosis or advice ({found.group(0).lower()!r})"
    for pattern, kind in ((CONDITIONS, "a condition"), (DOSING, "a dose")):
        for match in pattern.finditer(sentence.text):
            if match.group(0).lower() not in lowered:
                return f"names {kind} that its cited facts do not ({match.group(0).lower()!r})"
    return None


def check_summary(
    draft: SummaryDraft, items: Sequence[BriefingItem]
) -> tuple[BriefingSummary | None, list[str]]:
    """The checked summary, or None and every reason it was dropped."""
    by_id = {item.fact_id: item for item in items if item.fact_id is not None}
    reasons: list[str] = []
    if not draft.sentences:
        reasons.append("the summary has no sentence")
    if len(draft.sentences) > MAX_SENTENCES:
        reasons.append(f"the summary has more than {MAX_SENTENCES} sentences")
    for number, sentence in enumerate(draft.sentences, start=1):
        if (problem := sentence_problem(sentence, by_id)) is not None:
            reasons.append(f"sentence {number} {problem}")
    if reasons:
        return None, reasons
    sentences = [
        BriefingSentence(text=s.text.strip(), fact_ids=list(dict.fromkeys(s.fact_ids)))
        for s in draft.sentences
    ]
    return BriefingSummary(sentences=sentences), []


@dataclass(frozen=True)
class Summarizer:
    """`provider` builds the summary provider; it may raise `ProviderUnavailable` (no API key)."""

    provider: Callable[[], ChatProvider]
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    async def __call__(self, briefing: Briefing) -> SummaryOutcome:
        items = fact_items(briefing)
        if not items:
            return SummaryOutcome(SummaryStatus.disabled, reasons=["no fact to summarise"])
        try:
            provider = self.provider()
            async with asyncio.timeout(self.timeout_seconds):
                outcome = await structured(
                    provider,
                    build_messages(items),
                    SummaryDraft,
                    temperature=0.0,
                    max_tokens=MAX_TOKENS,
                )
        except Exception as exc:  # fail safe: no key, server down, timeout, cassette miss
            reason = f"summary provider unavailable ({type(exc).__name__})"
            logger.warning("briefing.summary_disabled", extra={"reason": reason})
            return SummaryOutcome(SummaryStatus.disabled, reasons=[reason])
        if isinstance(outcome, StructuredFailure):
            reasons = ["the model's reply did not validate twice"]
        else:
            summary, reasons = check_summary(outcome.value, items)
            if summary is not None:
                return SummaryOutcome(
                    SummaryStatus.ok, summary=summary, prompt_version=PROMPT_VERSION
                )
        logger.warning(
            "briefing.summary_dropped",
            extra={"session_id": briefing.session_id, "reasons": reasons},
        )
        return SummaryOutcome(SummaryStatus.dropped, reasons=reasons, prompt_version=PROMPT_VERSION)


__all__ = [
    "PROMPT_VERSION",
    "TASK",
    "SummaryDraft",
    "SummaryOutcome",
    "Summarizer",
    "build_messages",
    "check_summary",
    "fact_items",
]
