"""Tapped and explicit answers become facts without a model (M2-04).

A tap on the kiosk's Yes / No / Not sure buttons, or on a number of the 0 to 10 scale, sends the
button's label as the answer text. The label maps straight to the fact for the question's key:
PATIENT_STATED, citing the whole answer turn. A label this module does not recognise returns None,
and the answer goes through the extractor as typed or spoken text would.

A typed or spoken answer to a yes/no question is read the same way when it is explicit
(`explicit_answer`, SHE-293): a model that is slow or down must not lose a parent's "I am not
sure." to "Is your child hard to wake?". The reading is asymmetric, because a missed danger answer
is the worst failure: an answer that is, or begins with, "not sure" is unknown; one that begins
with "yes" is yes; only an answer that is nothing but "no" is no. "No, but she is very hard to
wake" is never read as no here: the extractor reads it.
"""

from __future__ import annotations

import re
from typing import Final

from pydantic import ValidationError

from careflow_contracts.generated.fact_schema import IntegerFact, TriState, TriStateFact
from careflow_contracts.generated.structured_facts_schema import FactKey, Turn
from careflow_intake import arabic
from careflow_intake.facts import FactEntry, fact_class, stable_fact_id

# The kiosk's button labels (apps/kiosk/messages/*.json `intake.answers`), and close variants.
YES: Final = frozenset({"yes"}) | arabic.YES
NO: Final = frozenset({"no"}) | arabic.NO
UNSURE: Final = frozenset({"not sure", "unsure", "don't know"}) | arabic.UNSURE

# Explicit answers in words (typed or spoken), matched on the normalised answer.
EXPLICIT_UNSURE: Final = (
    frozenset(
        {
            "not sure",
            "not really sure",
            "not certain",
            "unsure",
            "i am not sure",
            "i'm not sure",
            "im not sure",
            "i am not certain",
            "i'm not certain",
            "i don't know",
            "i dont know",
            "i do not know",
            "don't know",
            "dont know",
            "do not know",
            "i dunno",
            "dunno",
            "no idea",
            "i have no idea",
            "maybe",
            "perhaps",
            "possibly",
        }
    )
    | arabic.UNSURE
)
EXPLICIT_YES: Final = frozenset({"yes", "yeah", "yep", "yup", "yea"}) | arabic.YES
EXPLICIT_NO: Final = frozenset({"no", "nope", "nah", "not at all"}) | arabic.NO
# "No, she isn't." / "no he does not": a bare no with only its own negation after it.
_NO_AND_NEGATION: Final = re.compile(
    r"^(?:no|nope)[ ,]+(?:she|he|they|it|i|we)(?:'s| is| am| are| does| do| has| have| was| did)?"
    r"(?:n't| not)$"
)
_ENDS: Final = " \t\n.,;:!-—،؛"
_BREAK: Final = frozenset(" \t\n.,;:!?-—،؛؟")
_QUESTION: Final = frozenset("?؟")
_FIRST_WORD_END: Final = re.compile(r"[\s.,;:!\-—،؛]")
# Words said before "I don't know" that do not change it: "والله ما أدري" (honestly, I don't
# know), "um, not sure". The Arabic eval (M2-03) found the model reading "والله ما أدري، ما
# انتبهت" as no. They are skipped only before an unsure phrase; yes and no are read as before.
_FILLERS: Final = frozenset({"um", "umm", "uh", "well", "honestly"}) | frozenset(
    arabic.normalise(word) for word in ("والله", "يعني", "امم", "اممم", "طيب", "بصراحة")
)


def tap_value(key: FactKey, text: str) -> str | int | None:
    """The fact value a tapped label means for `key`, or None."""
    cls = fact_class(key)
    label = arabic.normalise(text.strip()).strip(" .!")
    if issubclass(cls, TriStateFact):
        if label in YES:
            return TriState.yes.value
        if label in NO:
            return TriState.no.value
        if label in UNSURE:
            return TriState.unknown.value
        return None
    if issubclass(cls, IntegerFact):
        digits = label.translate(arabic.DIGITS)
        return int(digits) if digits.isdigit() else None
    return None


def _said(text: str) -> str:
    """The answer normalised for matching: Arabic-normalised, casefolded, one space between
    words, typographic apostrophes as ', and no punctuation at either end."""
    said = arabic.normalise(text).replace("’", "'").replace("‘", "'")
    return " ".join(said.split()).strip(_ENDS)


def _starts_with(said: str, phrase: str) -> bool:
    """`said` is `phrase`, or begins with it followed by a space or punctuation."""
    return said == phrase or (said.startswith(phrase) and said[len(phrase)] in _BREAK)


def explicit_answer(text: str) -> tuple[TriState, bool] | None:
    """An explicit yes / no / not sure in `text`, and whether that is all it says; or None.

    Unsure first, so "لا أعرف" ("I don't know", which begins with "no") and "no idea" are unknown.
    An answer with a question mark is only read as unsure: "yes?" may not be an answer.
    """
    said = _said(text)
    if not said:
        return None
    unsure = _after_fillers(said)
    for phrase in EXPLICIT_UNSURE:
        if _starts_with(unsure, phrase):
            return TriState.unknown, _rest_is_empty(unsure, phrase)
    if _QUESTION & set(said):
        return None
    first = _FIRST_WORD_END.split(said, maxsplit=1)[0]
    if first in EXPLICIT_YES:
        return TriState.yes, _rest_is_empty(said, first)
    if said in EXPLICIT_NO or _NO_AND_NEGATION.match(said):
        return TriState.no, True
    return None


def _after_fillers(said: str) -> str:
    """`said` without the filler words (`_FILLERS`) it begins with."""
    while True:
        first = _FIRST_WORD_END.split(said, maxsplit=1)[0]
        if first not in _FILLERS or first == said:
            return said
        said = said[len(first) :].lstrip(_ENDS)


def _rest_is_empty(said: str, phrase: str) -> bool:
    """Nothing but spaces and punctuation after `phrase` ("not sure?" says nothing more)."""
    return not said[len(phrase) :].strip(_ENDS + "?؟")


def answer_fact(turn: Turn, key: FactKey, value: str | int) -> FactEntry | None:
    """The PATIENT_STATED fact `value` for `key`, citing the whole answer turn; None when the
    value does not fit the contract (e.g. a pain score of 11)."""
    fact = {
        "fact_id": stable_fact_id(turn.turn_id, turn.created_at.isoformat(), key.value),
        "provenance": "PATIENT_STATED",
        "source": {
            "kind": "transcript_span",
            "turn_id": turn.turn_id,
            "start": 0,
            "end": len(turn.text),
            "quote": turn.text[:1000],
        },
        "recorded_at": turn.created_at,
        "value": value,
    }
    try:
        return FactEntry.model_validate({"key": key, "fact": fact})
    except ValidationError:
        return None


def tap_fact(turn: Turn, key: FactKey) -> FactEntry | None:
    """The PATIENT_STATED fact for a tapped answer, or None when the label is not a tap answer
    for this key (or its value does not fit the contract, e.g. a pain score of 11)."""
    value = tap_value(key, turn.text)
    return None if value is None else answer_fact(turn, key, value)


def explicit_fact(turn: Turn, key: FactKey) -> tuple[FactEntry, bool] | None:
    """The fact for an explicit yes / no / not sure to a yes/no question, and whether the answer
    says nothing else; None for any other key or answer."""
    if not issubclass(fact_class(key), TriStateFact):
        return None
    found = explicit_answer(turn.text)
    if found is None:
        return None
    value, whole = found
    entry = answer_fact(turn, key, value.value)
    return None if entry is None else (entry, whole)


__all__ = [
    "answer_fact",
    "explicit_answer",
    "explicit_fact",
    "tap_fact",
    "tap_value",
]
