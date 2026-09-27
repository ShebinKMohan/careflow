"""`facts_to_state`: StructuredFacts as the Laya state (input contract `laya-state/v1`).

Pure Python: no torch and no `laya` import, so the API and the kiosk export (M3-02) can use it.
The state is a small English dict with a fixed key order; each value carries its unit ("112 /min").
A missing fact is left out, never written as "unknown". `fact_ids` maps every key to the facts it
came from, so a reason can cite them (safety: every reason is traceable, and staff-only).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from careflow_contracts import StructuredFacts

STATE_VERSION: Final = "laya-state/v1"
#: The keys, in this order (M0-02 fields).
STATE_KEYS: Final = (
    "age",
    "sex",
    "chief_complaint",
    "pain_score",
    "heart_rate",
    "spo2",
    "temperature",
    "respiratory_rate",
    "systolic_bp",
    "consciousness",
)
#: The kiosk measures pulse, SpO2 and temperature; the nurse adds these (the `kiosk_partial` state).
NURSE_ONLY_KEYS: Final = ("respiratory_rate", "systolic_bp", "consciousness")
MAX_COMPLAINT_CHARS: Final = 200
#: The one question: KTAS level 1 (most urgent) to 5.
KTAS_QUESTION: Final = {
    "urgency": {
        "type": "choice",
        "instructions": "Which KTAS triage level fits this emergency department patient?",
        "criteria": {
            "level_1": "resuscitation: immediate, life-threatening",
            "level_2": "emergent: potential threat to life or limb",
            "level_3": "urgent: serious, needs treatment soon",
            "level_4": "less urgent: could wait one to two hours",
            "level_5": "non-urgent: could be seen later or elsewhere",
        },
    }
}
LEVEL_KEYS: Final = tuple(KTAS_QUESTION["urgency"]["criteria"])  # level_1 ... level_5
ACVPU_TEXT: Final = {
    "A": "alert",
    "C": "new confusion",
    "V": "responds to voice",
    "P": "responds to pain",
    "U": "unresponsive",
}
LABELS: Final = {
    "age": "Age",
    "sex": "Sex",
    "chief_complaint": "Complaint",
    "pain_score": "Pain score",
    "heart_rate": "Heart rate",
    "spo2": "SpO2",
    "temperature": "Temperature",
    "respiratory_rate": "Respiratory rate",
    "systolic_bp": "Systolic BP",
    "consciousness": "Consciousness",
}


@dataclass(frozen=True)
class LayaInput:
    state: dict[str, str]
    fact_ids: dict[str, list[str]] = field(default_factory=dict[str, list[str]])

    def without(self, *keys: str) -> LayaInput:
        drop = set(keys)
        return LayaInput(
            state={k: v for k, v in self.state.items() if k not in drop},
            fact_ids={k: v for k, v in self.fact_ids.items() if k not in drop},
        )

    def kiosk_partial(self) -> LayaInput:
        """What the kiosk knows before the nurse: no respiratory rate, BP or consciousness."""
        return self.without(*NURSE_ONLY_KEYS)


@dataclass(frozen=True)
class Reason:
    text: str
    fact_ids: list[str]


def _number(value: float) -> str:
    return f"{value:.0f}" if float(value).is_integer() else f"{value:g}"


def facts_to_state(facts: StructuredFacts) -> LayaInput:
    patient, vitals = facts.patient, facts.vitals
    found: dict[str, tuple[str, str]] = {}  # key -> (text, fact id)
    if patient is not None and patient.age_years is not None:
        found["age"] = (f"{patient.age_years.value} years", patient.age_years.fact_id)
    elif patient is not None and patient.age_months is not None:
        found["age"] = (f"{patient.age_months.value} months", patient.age_months.fact_id)
    if patient is not None and patient.sex is not None:
        found["sex"] = (str(patient.sex.value.code), patient.sex.fact_id)
    if facts.chief_complaint is not None:
        text = " ".join(facts.chief_complaint.value.split())[:MAX_COMPLAINT_CHARS]
        found["chief_complaint"] = (text, facts.chief_complaint.fact_id)
    if facts.pain_score is not None:
        found["pain_score"] = (f"{facts.pain_score.value}/10", facts.pain_score.fact_id)
    if vitals is not None:
        if vitals.heart_rate is not None:
            found["heart_rate"] = (
                f"{_number(vitals.heart_rate.value)} /min",
                vitals.heart_rate.fact_id,
            )
        if vitals.spo2 is not None:
            found["spo2"] = (f"{_number(vitals.spo2.value)} %", vitals.spo2.fact_id)
        if vitals.temperature_c is not None:
            found["temperature"] = (
                f"{float(vitals.temperature_c.value):.1f} °C",
                vitals.temperature_c.fact_id,
            )
        if vitals.respiratory_rate is not None:
            found["respiratory_rate"] = (
                f"{_number(vitals.respiratory_rate.value)} /min",
                vitals.respiratory_rate.fact_id,
            )
        if vitals.systolic_bp is not None:
            found["systolic_bp"] = (
                f"{_number(vitals.systolic_bp.value)} mmHg",
                vitals.systolic_bp.fact_id,
            )
        if vitals.acvpu is not None:
            code = str(getattr(vitals.acvpu.value.code, "value", vitals.acvpu.value.code))
            found["consciousness"] = (ACVPU_TEXT.get(code, code), vitals.acvpu.fact_id)
    return LayaInput(
        state={key: found[key][0] for key in STATE_KEYS if key in found},
        fact_ids={key: [found[key][1]] for key in STATE_KEYS if key in found},
    )


def reasons_from_keys(keys: list[str], laya_input: LayaInput) -> list[Reason]:
    """One reason per key, e.g. "Heart rate 128 /min raised urgency", citing its facts."""
    reasons = []
    for key in keys:
        value = laya_input.state.get(key)
        if value is None:
            continue
        label = LABELS.get(key, key.replace("_", " ").capitalize())
        text = (
            f"{label}: {value} raised urgency"
            if key == "chief_complaint"
            else (f"{label} {value} raised urgency")
        )
        reasons.append(Reason(text=text[:300], fact_ids=list(laya_input.fact_ids.get(key, []))))
    return reasons


def level_of(key: str) -> int:
    """`level_3` -> 3."""
    return int(key.rsplit("_", 1)[1])


__all__ = [
    "KTAS_QUESTION",
    "LEVEL_KEYS",
    "NURSE_ONLY_KEYS",
    "STATE_KEYS",
    "STATE_VERSION",
    "LayaInput",
    "Reason",
    "facts_to_state",
    "level_of",
    "reasons_from_keys",
]
