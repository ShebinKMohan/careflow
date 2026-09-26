"""Builders for synthetic StructuredFacts, for rules tests and scenario suites (M1-02, M1-18).

Test support only: evaluate() never uses it. No patient data.
"""

from __future__ import annotations

import itertools
from typing import Any, Final, get_args

from pydantic import BaseModel

from careflow_contracts import CodeFact, IntegerFact, NumberFact, StructuredFacts

CROCKFORD: Final = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
RECORDED_AT: Final = "2026-09-26T09:03:00Z"
UNITS: Final = {
    "heart_rate": "/min",
    "respiratory_rate": "/min",
    "spo2": "%",
    "temperature_c": "Cel",
    "systolic_bp": "mm[Hg]",
    "diastolic_bp": "mm[Hg]",
}
ACVPU_SYSTEM: Final = "https://careflow.example/fhir/CodeSystem/acvpu"
_ids = itertools.count(1)

type Json = dict[str, Any]


def fact_id() -> str:
    """A fresh valid fact id ('f_' + 26 Crockford base32 characters)."""
    n, digits = next(_ids), list[str]()
    for _ in range(26):
        n, rest = divmod(n, 32)
        digits.append(CROCKFORD[rest])
    return "f_" + "".join(reversed(digits))


def _source(provenance: str) -> Json:
    return {
        "DEVICE_MEASURED": {
            "kind": "device",
            "device_id": "sim_vitals_01",
            "reading_id": "rd_0001",
        },
        "NURSE_CONFIRMED": {"kind": "nurse", "staff_user_id": "nurse_demo_01"},
        "PATIENT_STATED": {
            "kind": "transcript_span",
            "turn_id": "turn_01",
            "start": 0,
            "end": 10,
            "quote": "synthetic",
        },
        "EHR": {"kind": "fhir", "resource_type": "Patient", "id": "persona-test"},
    }[provenance]


def fact(value: Any, provenance: str, recorded_at: str = RECORDED_AT, **extra: Any) -> Json:
    return {
        "fact_id": fact_id(),
        "provenance": provenance,
        "source": _source(provenance),
        "recorded_at": recorded_at,
        "value": value,
        **extra,
    }


def vital(
    name: str, value: Any, provenance: str = "DEVICE_MEASURED", recorded_at: str = RECORDED_AT
) -> Json:
    if name == "acvpu":
        return fact({"system": ACVPU_SYSTEM, "code": value}, provenance, recorded_at)
    if name == "on_supplemental_o2":
        return fact(value, provenance, recorded_at)
    return fact(value, provenance, recorded_at, unit=UNITS[name])


def build(
    *,
    vitals: dict[str, Any] | None = None,
    nurse_vitals: dict[str, Any] | None = None,
    infection: dict[str, str] | None = None,
    symptoms: list[str] | None = None,
    age: int | None = None,
    language: str = "en",
) -> StructuredFacts:
    """Device vitals, nurse vitals (they replace device ones), infection answers and symptoms."""
    body: Json = {
        "session_id": "ses_rules_test",
        "clinic_id": "clinic_demo",
        "language": language,
        "extraction_status": "complete",
    }
    all_vitals = {name: vital(name, value) for name, value in (vitals or {}).items()}
    all_vitals |= {
        name: vital(name, value, "NURSE_CONFIRMED") for name, value in (nurse_vitals or {}).items()
    }
    if all_vitals:
        body["vitals"] = all_vitals
    if infection:
        body["infection"] = {key: fact(value, "PATIENT_STATED") for key, value in infection.items()}
    if symptoms:
        body["symptoms"] = [{"name": fact(name, "PATIENT_STATED")} for name in symptoms]
    if age is not None:
        body["patient"] = {"age_years": fact(age, "EHR")}
    return StructuredFacts.model_validate(body)


# --- Scenario files (eval/scenarios/rules/*.yaml; reused by the M1-18 eval harness) ----------


def _fact_class(key: str) -> type[BaseModel]:
    """The contract model of the fact at `group.field` (e.g. vitals.heart_rate -> HeartRateFact)."""
    model: type[BaseModel] = StructuredFacts
    for part in key.split("."):
        annotation = model.model_fields[part].annotation
        candidates = [
            arg for arg in (get_args(annotation) or (annotation,)) if isinstance(arg, type)
        ]
        found = [arg for arg in candidates if issubclass(arg, BaseModel)]
        if not found:
            raise KeyError(f"not a fact path: {key}")
        model = found[0]
    return model


def _typed_value(key: str, raw: str) -> Any:
    """Scenario values are YAML strings; convert them to the type the contract expects."""
    cls = _fact_class(key)
    if issubclass(cls, NumberFact):
        return float(raw)
    if issubclass(cls, IntegerFact):
        return int(raw)
    if issubclass(cls, CodeFact):
        return {"system": ACVPU_SYSTEM, "code": raw} if key == "vitals.acvpu" else raw
    return raw  # tri-state ("yes" / "no" / "unknown") and text


def facts_from_entries(
    entries: list[dict[str, str]],
    *,
    session_id: str = "ses_scenario",
    language: str = "en",
) -> StructuredFacts:
    """Build StructuredFacts from `{path, value, provenance, recorded_at}` entries. `symptoms`
    entries add one symptom each; `chief_complaint` is a text fact; every other path is a leaf
    fact of the contract (FactKey)."""
    body: Json = {
        "session_id": session_id,
        "clinic_id": "clinic_demo",
        "language": language,
        "extraction_status": "complete",
    }
    for entry in entries:
        path, raw = entry["path"], entry["value"]
        provenance, recorded_at = entry["provenance"], entry["recorded_at"]
        if path == "symptoms":
            symptom = {"name": fact(raw, provenance, recorded_at)}
            body.setdefault("symptoms", []).append(symptom)
            continue
        if path == "chief_complaint":
            body["chief_complaint"] = fact(raw, provenance, recorded_at)
            continue
        group, _, name = path.partition(".")
        value = _typed_value(path, raw)
        extra = {"unit": UNITS[name]} if name in UNITS and group == "vitals" else {}
        body.setdefault(group, {})[name] = fact(value, provenance, recorded_at, **extra)
    return StructuredFacts.model_validate(body)
