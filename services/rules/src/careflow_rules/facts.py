"""Small lookups over StructuredFacts shared by the rules (pure, no I/O)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from careflow_contracts import (
    FactBase,
    FactKey,
    NeededFact,
    NeededFactAudience,
    Provenance,
    StructuredFacts,
    TriState,
    TriStateFact,
)

MEASURED: Final = frozenset({Provenance.DEVICE_MEASURED, Provenance.NURSE_CONFIRMED})


def whole_number(value: float) -> int:
    """Half-up rounding to a whole number (repr keeps the decimal the reading was written with)."""
    return int(Decimal(repr(value)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def one_decimal(value: float) -> Decimal:
    """Half-up rounding to one decimal place (temperatures)."""
    return Decimal(repr(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def fact_at(facts: StructuredFacts, key: FactKey) -> FactBase | None:
    """The fact at a `group.field` path (e.g. `chest_pain.present`), or None when absent."""
    node: object = facts
    for part in key.value.split("."):
        node = getattr(node, part, None)
        if node is None:
            return None
    return node if isinstance(node, FactBase) else None


def is_yes(fact: FactBase | None) -> bool:
    return isinstance(fact, TriStateFact) and fact.value is TriState.yes


def is_unsure(fact: FactBase | None) -> bool:
    """A "Not sure" answer (TriState unknown)."""
    return isinstance(fact, TriStateFact) and fact.value is TriState.unknown


def stated_symptoms(facts: StructuredFacts) -> dict[str, str]:
    """Patient-stated symptom names mapped to their fact ids."""
    return {
        symptom.name.value: symptom.name.fact_id
        for symptom in facts.symptoms or []
        if symptom.name.provenance is Provenance.PATIENT_STATED
    }


def ask(
    facts: StructuredFacts,
    keys: Iterable[FactKey],
    audience: NeededFactAudience,
    reason: str,
) -> tuple[NeededFact, ...]:
    """Needed facts for the keys that are not answered yet, in order."""
    return tuple(
        NeededFact(key=key, audience=audience, reason=reason)
        for key in keys
        if fact_at(facts, key) is None
    )


def unique(ids: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(ids))


@dataclass(frozen=True, slots=True)
class AgeMonths:
    """Possible age in whole months: exact from `patient.age_months`, else the 12 months of the
    `patient.age_years` value. Thresholds use the end of the range that alerts sooner."""

    low: int
    high: int


def age_months(facts: StructuredFacts) -> tuple[AgeMonths, list[str]] | None:
    """The age range and the fact id behind it, or None when no age is known."""
    patient = facts.patient
    if patient is not None and patient.age_months is not None:
        months = patient.age_months.value
        return AgeMonths(months, months), [patient.age_months.fact_id]
    if patient is not None and patient.age_years is not None:
        years = patient.age_years.value
        return AgeMonths(12 * years, 12 * years + 11), [patient.age_years.fact_id]
    return None
