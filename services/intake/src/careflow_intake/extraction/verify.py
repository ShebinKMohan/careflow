"""Verify the model's facts against the transcript. Offsets are always computed here: models
miscount characters, and a fact without a verified span never reaches the state (safety rule 4).

Arabic (M2-03): quotes are matched after the Arabic normalisation in `careflow_intake.arabic`
(tatweel, diacritics, alef forms, NFC), with a map back to the original characters, so a span
always points into the patient's own turn text. Numbers may be Arabic-Indic digits or Arabic words.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from careflow_contracts.generated.extraction_output_schema import ExtractedFact
from careflow_contracts.generated.structured_facts_schema import Turn, TurnRole
from careflow_intake import arabic
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
_EDGE_PUNCTUATION: Final = " \t\n\"'`.,;:!?()[]{}،؛؟«»…"


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


def _normalise(text: str) -> tuple[str, list[int], list[int]]:
    """Casefolded, Arabic-normalised text with runs of whitespace collapsed to one space, plus
    where each of its characters starts and ends in the original. A dropped mark (a diacritic, a
    tatweel, a combining mark) belongs to the character before it, so a span keeps its marks."""
    chars: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    for index, char in enumerate(text):
        if char.isspace():
            if chars and chars[-1] != " ":
                chars.append(" ")
                starts.append(index)
                ends.append(index + 1)
            continue
        if arabic.is_dropped(char) or (unicodedata.combining(char) and chars):
            if chars and chars[-1] != " ":
                if not arabic.is_dropped(char):
                    composed = unicodedata.normalize("NFC", chars[-1] + char)
                    if len(composed) == 1:
                        chars[-1] = arabic.fold(composed)
                ends[-1] = index + 1
            continue
        for folded in arabic.fold(char):
            chars.append(folded)
            starts.append(index)
            ends.append(index + 1)
    if chars and chars[-1] == " ":
        chars.pop()
        starts.pop()
        ends.pop()
    return "".join(chars), starts, ends


def find_span(text: str, quote: str) -> tuple[int, int] | None:
    """Where `quote` is in `text`: an exact match first, then ignoring case, whitespace and the
    Arabic marks, then also ignoring punctuation at the quote's ends. None when it is not there."""
    start = text.find(quote)
    if start >= 0 and quote:
        return start, start + len(quote)
    haystack, starts, ends = _normalise(text)
    for candidate in (quote, quote.strip(_EDGE_PUNCTUATION)):
        needle, _, _ = _normalise(candidate)
        if not needle:
            continue
        position = haystack.find(needle)
        if position >= 0:
            return starts[position], ends[position + len(needle) - 1]
    return None


def numeric_value(value: bool | int | float | str) -> float | None:
    """The number in a value (a digit string counts); None for yes/no and text."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(value.strip().translate(arabic.DIGITS))
    except ValueError:
        return None


def number_in_quote(number: float, quote: str) -> bool:
    """The number appears in the quote as digits (7, 7.5, 7,5, ٧) or as an English or Arabic
    word from zero to ten."""
    if arabic.number_word_in(number, quote):
        return True
    text = quote.translate(arabic.DIGITS).casefold()
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
