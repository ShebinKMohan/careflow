"""Tapped answers become facts without a model (M2-04).

A tap on the kiosk's Yes / No / Not sure buttons, or on a number of the 0 to 10 scale, sends the
button's label as the answer text. The label maps straight to the fact for the question's key:
PATIENT_STATED, citing the whole answer turn. A label this module does not recognise returns None,
and the answer goes through the extractor as typed or spoken text would.
"""

from __future__ import annotations

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


def tap_fact(turn: Turn, key: FactKey) -> FactEntry | None:
    """The PATIENT_STATED fact for a tapped answer, or None when the label is not a tap answer
    for this key (or its value does not fit the contract, e.g. a pain score of 11)."""
    value = tap_value(key, turn.text)
    if value is None:
        return None
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


__all__ = ["tap_fact", "tap_value"]
