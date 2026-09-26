"""One feature path for training and serving: KTAS row -> StructuredFacts -> feature vector.

`row_to_facts` follows `config/ktas_mapping.yaml`. `facts_to_features` is the only feature
function: training calls it on the facts built from KTAS rows, and serving (M1-05
suggest_urgency) calls it on the facts from the intake conversation. Leakage columns
(KTAS_RN, KTAS_expert, Diagnosis in ED, Disposition, Error_group, Length of stay_min,
KTAS duration_min, mistriage) never reach it.
"""

from __future__ import annotations

import math
import re
from functools import cache
from typing import Any, Final

import numpy as np
import yaml

from careflow_contracts import StructuredFacts
from careflow_triage.paths import CONFIG_DIR

#: The model's inputs, in order. A test pins this list (the leakage guard).
FEATURE_NAMES: Final = (
    "age_years",
    "sex_male",
    "arrival_mode",
    "injury",
    "acvpu",
    "pain_present",
    "pain_score",
    "systolic_bp",
    "diastolic_bp",
    "heart_rate",
    "respiratory_rate",
    "temperature_c",
    "spo2",
    "complaint_category",
)
ARRIVAL_CODES: Final = (
    "walking",
    "private_vehicle",
    "public_ambulance",
    "private_ambulance",
    "other",
)
ACVPU_CODES: Final = ("A", "C", "V", "P", "U")
CATEGORICAL: Final = frozenset({"arrival_mode", "acvpu", "complaint_category"})
RECORDED_AT: Final = "2019-01-01T00:00:00Z"  # KTAS rows carry no time; a fixed placeholder
SEX_SYSTEM: Final = "http://hl7.org/fhir/administrative-gender"
ARRIVAL_SYSTEM: Final = "https://careflow.example/fhir/CodeSystem/arrival-mode"
ACVPU_SYSTEM: Final = "https://careflow.example/fhir/CodeSystem/acvpu"
COMPLAINT_SYSTEM: Final = "https://careflow.example/fhir/CodeSystem/complaint-category"
CROCKFORD: Final = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


@cache
def mapping() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(
        (CONFIG_DIR / "ktas_mapping.yaml").read_text(encoding="utf-8")
    )
    return data


def complaint_codes() -> tuple[str, ...]:
    return tuple(rule["code"] for rule in mapping()["complaint_categories"])


def complaint_category(text: str) -> tuple[str, str]:
    """(code, display) of the first rule whose keyword starts a word of `text` (whole word when
    the keyword ends in a space); `other` when nothing matches."""
    words = re.findall(r"[a-z]+", text.lower())
    for rule in mapping()["complaint_categories"]:
        for keyword in rule["keywords"]:
            whole = keyword.endswith(" ")
            key = keyword.strip()
            if any(word == key if whole else word.startswith(key) for word in words):
                return rule["code"], rule["display"]
    return "other", "Other"


def _fact_id(row: int, slot: int) -> str:
    n, digits = row * 32 + slot + 1, []
    for _ in range(26):
        n, rest = divmod(n, 32)
        digits.append(CROCKFORD[rest])
    return "f_" + "".join(reversed(digits))


def _present(value: Any) -> bool:
    return (
        value is not None and not (isinstance(value, float) and math.isnan(value)) and value != ""
    )


def row_to_facts(row: dict[str, Any], *, index: int = 0) -> StructuredFacts:
    """One KTAS row as StructuredFacts (nurse-recorded vitals and answers, EHR age and sex)."""
    columns = mapping()["columns"]
    nurse = {"kind": "nurse", "staff_user_id": "ktas_triage_nurse"}
    ehr = {"kind": "fhir", "resource_type": "Patient", "id": f"ktas-{index}"}
    body: dict[str, Any] = {
        "session_id": f"ktas_{index}",
        "clinic_id": "ktas_dataset",
        "language": "en",
        "extraction_status": "complete",
    }
    slot = 0

    def fact(value: Any, source: dict[str, Any], provenance: str, **extra: Any) -> dict[str, Any]:
        nonlocal slot
        slot += 1
        return {
            "fact_id": _fact_id(index, slot),
            "provenance": provenance,
            "source": source,
            "recorded_at": RECORDED_AT,
            "value": value,
            **extra,
        }

    for column, spec in columns.items():
        raw: Any = row.get(column)
        if not _present(raw):
            continue
        path, kind = spec["path"], spec["kind"]
        if kind == "complaint":
            code, display = complaint_category(str(raw))
            model = {"kind": "model", "model_id": "ktas_keyword_map", "model_version": "1"}
            value = {"system": COMPLAINT_SYSTEM, "code": code, "display": display}
            body["complaint_category"] = fact(value, model, "MODEL_INFERRED")
            continue
        code = spec.get("codes", {}).get(int(raw)) if "codes" in spec else None
        if "codes" in spec and code is None:
            continue  # an unknown code is treated as not recorded
        if kind in ("integer", "number"):
            low, high = RANGES.get(path, (0.0, math.inf))
            if not low <= float(raw) <= high:
                continue  # implausible for the contract: treated as not recorded
            value: Any = int(float(raw)) if kind == "integer" else float(raw)
        elif kind == "sex":
            value = {"system": SEX_SYSTEM, "code": code}
        elif kind == "arrival_mode":
            value = {"system": ARRIVAL_SYSTEM, "code": code}
        elif kind == "acvpu":
            value = {"system": ACVPU_SYSTEM, "code": code}
        else:  # tristate
            value = code
        source, provenance = (
            (ehr, "EHR") if path.startswith("patient.") else (nurse, "NURSE_CONFIRMED")
        )
        extra = {"unit": UNITS[path]} if path in UNITS else {}
        group, _, name = path.partition(".")
        if name:
            body.setdefault(group, {})[name] = fact(value, source, provenance, **extra)
        else:
            body[group] = fact(value, source, provenance, **extra)
    return StructuredFacts.model_validate(body)


#: Contract limits; a value outside them is treated as not recorded (default: not negative).
RANGES: Final = {
    "vitals.spo2": (0.0, 100.0),
    "pain_score": (0.0, 10.0),
    "patient.age_years": (0.0, 150.0),
}

UNITS: Final = {
    "vitals.heart_rate": "/min",
    "vitals.respiratory_rate": "/min",
    "vitals.spo2": "%",
    "vitals.temperature_c": "Cel",
    "vitals.systolic_bp": "mm[Hg]",
    "vitals.diastolic_bp": "mm[Hg]",
}


def _tri(fact: Any) -> float:
    if fact is None or fact.value.value == "unknown":
        return math.nan
    return 1.0 if fact.value.value == "yes" else 0.0


def _num(fact: Any) -> float:
    return math.nan if fact is None else float(fact.value)


def _index(code: str | None, codes: tuple[str, ...]) -> float:
    return float(codes.index(code)) if code in codes else math.nan


def facts_to_features(facts: StructuredFacts) -> np.ndarray:
    """The feature vector (FEATURE_NAMES order); NaN when a fact is missing or unknown.
    Categorical features are category indices."""
    patient, vitals = facts.patient, facts.vitals
    sex = patient.sex.value.code if patient and patient.sex else None
    features = {
        "age_years": _num(patient.age_years if patient else None),
        "sex_male": math.nan if sex not in ("male", "female") else float(sex == "male"),
        "arrival_mode": _index(
            facts.arrival_mode.value.code.value if facts.arrival_mode else None, ARRIVAL_CODES
        ),
        "injury": _tri(facts.injury),
        "acvpu": _index(
            vitals.acvpu.value.code.value if vitals and vitals.acvpu else None, ACVPU_CODES
        ),
        "pain_present": _tri(facts.pain_present),
        "pain_score": _num(facts.pain_score),
        "systolic_bp": _num(vitals.systolic_bp if vitals else None),
        "diastolic_bp": _num(vitals.diastolic_bp if vitals else None),
        "heart_rate": _num(vitals.heart_rate if vitals else None),
        "respiratory_rate": _num(vitals.respiratory_rate if vitals else None),
        "temperature_c": _num(vitals.temperature_c if vitals else None),
        "spo2": _num(vitals.spo2 if vitals else None),
        "complaint_category": _index(
            facts.complaint_category.value.code if facts.complaint_category else None,
            complaint_codes(),
        ),
    }
    return np.array([features[name] for name in FEATURE_NAMES], dtype=float)


def feature_fact_ids(facts: StructuredFacts) -> dict[str, list[str]]:
    """The fact ids behind each feature (for the reasons of a suggestion)."""
    patient, vitals = facts.patient, facts.vitals
    sources: dict[str, Any] = {
        "age_years": patient.age_years if patient else None,
        "sex_male": patient.sex if patient else None,
        "arrival_mode": facts.arrival_mode,
        "injury": facts.injury,
        "acvpu": vitals.acvpu if vitals else None,
        "pain_present": facts.pain_present,
        "pain_score": facts.pain_score,
        "systolic_bp": vitals.systolic_bp if vitals else None,
        "diastolic_bp": vitals.diastolic_bp if vitals else None,
        "heart_rate": vitals.heart_rate if vitals else None,
        "respiratory_rate": vitals.respiratory_rate if vitals else None,
        "temperature_c": vitals.temperature_c if vitals else None,
        "spo2": vitals.spo2 if vitals else None,
        "complaint_category": facts.complaint_category,
    }
    return {name: [fact.fact_id] if fact is not None else [] for name, fact in sources.items()}
