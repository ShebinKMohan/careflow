"""NEWS2, the National Early Warning Score 2 (Royal College of Physicians, NEWS2 (2017)).

SpO2 Scale 1 only. Temperature is rounded to one decimal place and every other value to a whole
number (half up) before the table lookup. Only the parameters present are scored; with any of the
seven missing the result is `partial` and underestimates risk, so it must always be labelled
partial and never shown as a full score. Not applicable under 16 (children: NICE NG143, M1-02).
Pure functions: no I/O, no clock, no randomness.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from careflow_contracts import (
    AcvpuCode,
    FactBase,
    FactKey,
    Provenance,
    StructuredFacts,
    TriState,
    Vitals,
)

type Parameter = Literal[
    "respiratory_rate",
    "spo2",
    "systolic_bp",
    "heart_rate",
    "acvpu",
    "temperature_c",
    "on_supplemental_o2",
]
type RiskBand = Literal["low", "low-medium", "medium", "high"]

#: The seven NEWS2 inputs, in the order missing ones are listed (the nurse adds the last four).
PARAMETERS: Final[tuple[Parameter, ...]] = (
    "respiratory_rate",
    "spo2",
    "systolic_bp",
    "heart_rate",
    "acvpu",
    "temperature_c",
    "on_supplemental_o2",
)
FACT_KEYS: Final[dict[Parameter, FactKey]] = {p: FactKey(f"vitals.{p}") for p in PARAMETERS}
NOT_APPLICABLE_UNDER_16: Final = "NEWS2 not applicable under 16"
ADULT_AGE_YEARS: Final = 16


@dataclass(frozen=True, slots=True)
class News2Inputs:
    """Plain values for the calculator; None means not measured (or unknown)."""

    respiratory_rate: float | None = None
    spo2: float | None = None
    systolic_bp: float | None = None
    heart_rate: float | None = None
    acvpu: AcvpuCode | None = None
    temperature_c: float | None = None
    on_supplemental_o2: bool | None = None
    age_years: int | None = None


class News2Result(BaseModel):
    model_config = ConfigDict(frozen=True)

    total: int
    subscores: dict[Parameter, int]
    missing: list[FactKey]
    partial: bool
    any_single_3: bool
    risk_band: RiskBand


def _whole(value: float) -> int:
    """Half-up rounding to a whole number (repr keeps the decimal the reading was written with)."""
    return int(Decimal(repr(value)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _one_decimal(value: float) -> Decimal:
    return Decimal(repr(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def score_respiratory_rate(per_min: float) -> int:
    rate = _whole(per_min)
    if rate <= 8:
        return 3
    if rate <= 11:
        return 1
    if rate <= 20:
        return 0
    if rate <= 24:
        return 2
    return 3


def score_spo2_scale1(percent: float) -> int:
    spo2 = _whole(percent)
    if spo2 <= 91:
        return 3
    if spo2 <= 93:
        return 2
    if spo2 <= 95:
        return 1
    return 0


def score_supplemental_o2(on_oxygen: bool) -> int:
    return 2 if on_oxygen else 0


def score_systolic_bp(mm_hg: float) -> int:
    pressure = _whole(mm_hg)
    if pressure <= 90:
        return 3
    if pressure <= 100:
        return 2
    if pressure <= 110:
        return 1
    if pressure <= 219:
        return 0
    return 3


def score_pulse(per_min: float) -> int:
    pulse = _whole(per_min)
    if pulse <= 40:
        return 3
    if pulse <= 50:
        return 1
    if pulse <= 90:
        return 0
    if pulse <= 110:
        return 1
    if pulse <= 130:
        return 2
    return 3


def score_consciousness(level: AcvpuCode) -> int:
    return 0 if level is AcvpuCode.A else 3


def score_temperature(celsius: float) -> int:
    temperature = _one_decimal(celsius)
    if temperature <= Decimal("35.0"):
        return 3
    if temperature <= Decimal("36.0"):
        return 1
    if temperature <= Decimal("38.0"):
        return 0
    if temperature <= Decimal("39.0"):
        return 1
    return 2


def risk_band(total: int, any_single_3: bool) -> RiskBand:
    if total >= 7:
        return "high"
    if total >= 5:
        return "medium"
    return "low-medium" if any_single_3 else "low"


def score_news2(inputs: News2Inputs) -> News2Result | None:
    """Score the parameters present. None (not applicable) under 16: see NOT_APPLICABLE_UNDER_16."""
    if inputs.age_years is not None and inputs.age_years < ADULT_AGE_YEARS:
        return None
    subscores: dict[Parameter, int] = {}
    if inputs.respiratory_rate is not None:
        subscores["respiratory_rate"] = score_respiratory_rate(inputs.respiratory_rate)
    if inputs.spo2 is not None:
        subscores["spo2"] = score_spo2_scale1(inputs.spo2)
    if inputs.systolic_bp is not None:
        subscores["systolic_bp"] = score_systolic_bp(inputs.systolic_bp)
    if inputs.heart_rate is not None:
        subscores["heart_rate"] = score_pulse(inputs.heart_rate)
    if inputs.acvpu is not None:
        subscores["acvpu"] = score_consciousness(inputs.acvpu)
    if inputs.temperature_c is not None:
        subscores["temperature_c"] = score_temperature(inputs.temperature_c)
    if inputs.on_supplemental_o2 is not None:
        subscores["on_supplemental_o2"] = score_supplemental_o2(inputs.on_supplemental_o2)
    total = sum(subscores.values())
    any_single_3 = any(score == 3 for score in subscores.values())
    missing = [FACT_KEYS[p] for p in PARAMETERS if p not in subscores]
    return News2Result(
        total=total,
        subscores=subscores,
        missing=missing,
        partial=bool(missing),
        any_single_3=any_single_3,
        risk_band=risk_band(total, any_single_3),
    )


def news2_inputs(facts: StructuredFacts) -> News2Inputs:
    """The NEWS2 inputs in `facts` (one current fact per vitals path; an unknown oxygen answer is
    treated as not measured)."""
    vitals = facts.vitals or Vitals()
    oxygen = vitals.on_supplemental_o2
    age = facts.patient.age_years if facts.patient else None
    return News2Inputs(
        respiratory_rate=vitals.respiratory_rate.value if vitals.respiratory_rate else None,
        spo2=vitals.spo2.value if vitals.spo2 else None,
        systolic_bp=vitals.systolic_bp.value if vitals.systolic_bp else None,
        heart_rate=vitals.heart_rate.value if vitals.heart_rate else None,
        acvpu=vitals.acvpu.value.code if vitals.acvpu else None,
        temperature_c=vitals.temperature_c.value if vitals.temperature_c else None,
        on_supplemental_o2=None
        if oxygen is None or oxygen.value is TriState.unknown
        else oxygen.value is TriState.yes,
        age_years=age.value if age else None,
    )


def fact_ids(vitals: Vitals | None) -> dict[Parameter, str]:
    """The fact id behind each NEWS2 input that is present."""
    ids: dict[Parameter, str] = {}
    for parameter in PARAMETERS:
        fact: FactBase | None = getattr(vitals, parameter) if vitals else None
        if fact is not None:
            ids[parameter] = fact.fact_id
    return ids


def merge_vitals(current: Vitals | None, update: Vitals) -> Vitals:
    """Combine two readings per vitals path: a nurse value always wins over a device value (even
    an older one); otherwise the more recent fact wins. Callers merge before calling evaluate()."""
    base = current or Vitals()
    merged: dict[str, object] = {}
    for name in Vitals.model_fields:
        old, new = getattr(base, name), getattr(update, name)
        if old is None or new is None:
            merged[name] = new if old is None else old
            continue
        old_nurse = old.provenance is Provenance.NURSE_CONFIRMED
        new_nurse = new.provenance is Provenance.NURSE_CONFIRMED
        if old_nurse != new_nurse:
            merged[name] = old if old_nurse else new
        else:
            merged[name] = new if new.recorded_at >= old.recorded_at else old
    return Vitals.model_validate(merged)
