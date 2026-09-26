"""Verify the model's facts against the transcript. Offsets are always computed here: models
miscount characters, and a fact without a verified span never reaches the state (safety rule 4)."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from careflow_contracts.generated.extraction_output_schema import ExtractedFact
from careflow_contracts.generated.structured_facts_schema import Turn, TurnRole
from careflow_intake.extraction.prompt import turn_labels

logger = logging.getLogger("careflow_intake.extraction")

NUMBER_WORDS: Final = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_EDGE_PUNCTUATION: Final = " \t\n\"'`.,;:!?()[]{}"


@dataclass(frozen=True)
class VerifiedFact:
    fact: ExtractedFact
    turn: Turn
    start: int
    end: int

    @property
    def quote(self) -> str:
        """The patient's exact words at the verified span."""
        return self.turn.text[self.start : self.end]


@dataclass(frozen=True)
class DroppedFact:
    fact: ExtractedFact
    reason: str


def _normalise(text: str) -> tuple[str, list[int]]:
    """Casefolded text with runs of whitespace collapsed to one space, plus the original index of
    every character of the result."""
    chars: list[str] = []
    origin: list[int] = []
    for index, char in enumerate(text):
        if char.isspace():
            if chars and chars[-1] != " ":
                chars.append(" ")
                origin.append(index)
            continue
        for folded in char.casefold():
            chars.append(folded)
            origin.append(index)
    if chars and chars[-1] == " ":
        chars.pop()
        origin.pop()
    return "".join(chars), origin


def find_span(text: str, quote: str) -> tuple[int, int] | None:
    """Where `quote` is in `text`: an exact match first, then ignoring case and whitespace, then
    also ignoring punctuation at the quote's ends. None when it is not there."""
    start = text.find(quote)
    if start >= 0 and quote:
        return start, start + len(quote)
    haystack, origin = _normalise(text)
    for candidate in (quote, quote.strip(_EDGE_PUNCTUATION)):
        needle, _ = _normalise(candidate)
        if not needle:
            continue
        position = haystack.find(needle)
        if position >= 0:
            return origin[position], origin[position + len(needle) - 1] + 1
    return None


def numeric_value(value: bool | int | float | str) -> float | None:
    """The number in a value (a digit string counts); None for yes/no and text."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(value.strip())
    except ValueError:
        return None


def number_in_quote(number: float, quote: str) -> bool:
    """The number appears in the quote as digits (7, 7.5, 7,5) or as a word from zero to ten."""
    text = quote.casefold()
    forms = {format(number, "g")}
    if number.is_integer():
        forms.add(str(int(number)))
    forms |= {form.replace(".", ",") for form in forms}
    for form in forms:
        if re.search(rf"(?<![\d.,]){re.escape(form)}(?![\d])", text):
            return True
    words = [word for word, value in NUMBER_WORDS.items() if value == number]
    return any(re.search(rf"\b{word}\b", text) for word in words)


def verify_facts(
    facts: Sequence[ExtractedFact], transcript: Sequence[Turn]
) -> tuple[list[VerifiedFact], list[DroppedFact]]:
    """Keep the facts whose quote is in the patient turn they cite (by label `t1..tn` or turn id)
    and whose number, if any, is in that quote. Every dropped fact is logged with its reason."""
    labels = turn_labels(transcript)
    by_id = {turn.turn_id: turn for turn in transcript}
    verified: list[VerifiedFact] = []
    dropped: list[DroppedFact] = []
    for fact in facts:
        reason: str | None = None
        turn = labels.get(fact.turn_id) or by_id.get(fact.turn_id)
        span = None
        if turn is None:
            reason = "unknown_turn"
        elif turn.role is not TurnRole.patient:
            reason = "not_a_patient_turn"
        elif (span := find_span(turn.text, fact.quote)) is None:
            reason = "quote_not_found"
        else:
            number = numeric_value(fact.value)
            quote = turn.text[span[0] : span[1]]
            if number is not None and not number_in_quote(number, quote):
                reason = "number_not_in_quote"
        if reason is not None or turn is None or span is None:
            dropped.append(DroppedFact(fact, reason or "unverified"))
            # No patient words in the log: the key, the turn label and the reason only.
            logger.info(
                "extraction.fact_dropped",
                extra={"key": fact.key.value, "turn": fact.turn_id, "reason": reason},
            )
            continue
        verified.append(VerifiedFact(fact, turn, span[0], span[1]))
    return verified, dropped


__all__ = [
    "DroppedFact",
    "VerifiedFact",
    "find_span",
    "number_in_quote",
    "numeric_value",
    "verify_facts",
]
