"""The codes the write-back uses. They are read from the M0-04 mapping table
(`packages/contracts/fhir/mapping.yaml`) and the intake Questionnaire where those carry them; the
few the table names only in its notes are constants here, next to the note they come from."""

from __future__ import annotations

import json
from functools import cache
from typing import Any, Final

import yaml

from careflow_integration.adapters.loader import REPO

type Json = dict[str, Any]

FHIR_CONTRACTS: Final = REPO / "packages" / "contracts" / "fhir"
MAPPING_FILE: Final = FHIR_CONTRACTS / "mapping.yaml"
QUESTIONNAIRE_FILE: Final = FHIR_CONTRACTS / "questionnaire-intake.json"

LOINC: Final = "http://loinc.org"
UCUM: Final = "http://unitsofmeasure.org"
OBSERVATION_CATEGORY: Final = "http://terminology.hl7.org/CodeSystem/observation-category"
# mapping.yaml vitals notes: category vital-signs; rules.news2_total notes: category survey.
VITAL_SIGNS: Final = {
    "system": OBSERVATION_CATEGORY,
    "code": "vital-signs",
    "display": "Vital Signs",
}
SURVEY: Final = {"system": OBSERVATION_CATEGORY, "code": "survey", "display": "Survey"}
# vitals.spo2 notes: a second coding for pulse oximetry.
SPO2_PULSE_OXIMETRY: Final = {
    "system": LOINC,
    "code": "59408-5",
    "display": "Oxygen saturation in Arterial blood by Pulse oximetry",
}
# vitals.systolic_bp notes: one panel Observation holds both components.
BP_PANEL: Final = {
    "system": LOINC,
    "code": "85354-9",
    "display": "Blood pressure panel with all children optional",
}
# vitals.acvpu notes: the UK Core ACVPU answers (SNOMED CT).
ACVPU_ANSWERS: Final = {
    "A": ("248234008", "Mentally alert"),
    "C": ("130987000", "Acute confusion"),
    "V": ("300202002", "Responds to voice"),
    "P": ("450847001", "Responds to pain"),
    "U": ("422768004", "Unresponsive"),
}
# alert notes: one Flag per Alert, category clinical.
FLAG_CLINICAL: Final = {
    "system": "http://terminology.hl7.org/CodeSystem/flag-category",
    "code": "clinical",
    "display": "Clinical",
}
# nurse_decision.priority notes (a CareFlow convention): level 1 -> S, 2 -> EM, 3 -> UR, 4-5 -> R.
LEVEL_TO_PRIORITY: Final = {1: "S", 2: "EM", 3: "UR", 4: "R", 5: "R"}
# session.encounter notes: the class comes from the hospital adapter (verified 27 Sep 2026).
ENCOUNTER_CLASS_DISPLAY: Final = {"AMB": "ambulatory", "EMER": "emergency"}
# nurse_decision.scale notes: component code triage-level#scale.
SCALE_DISPLAY: Final = {
    "KTAS": "Korean Triage and Acuity Scale",
    "ESI": "Emergency Severity Index",
    "CTAS": "Canadian Triage and Acuity Scale",
    "MTS": "Manchester Triage System",
}
# write_batch.provenance: the provenance-participant-type displays.
PARTICIPANT_DISPLAY: Final = {
    "performer": "Performer",
    "assembler": "Assembler",
    "author": "Author",
    "informant": "Informant",
}
# The issue's Task text for each task kind.
TASK_TEXT: Final = {"ecg_within_10_min": "12-lead ECG"}


@cache
def mapping() -> Json:
    body: Json = yaml.safe_load(MAPPING_FILE.read_text(encoding="utf-8"))
    return body


@cache
def questionnaire() -> Json:
    body: Json = json.loads(QUESTIONNAIRE_FILE.read_text(encoding="utf-8"))
    return body


def code_system(name: str) -> str:
    """A CareFlow CodeSystem URL by short name, e.g. `rule-id`."""
    systems: dict[str, str] = mapping()["careflow_code_systems"]
    return systems[name]


def entry(fact_path: str, resource: str) -> Json:
    """The one write entry for `fact_path` into `resource`."""
    found = [
        e
        for e in mapping()["entries"]
        if e["fact_path"] == fact_path and e["resource"] == resource and e["direction"] == "write"
    ]
    if len(found) != 1:
        raise LookupError(
            f"mapping.yaml has {len(found)} write entries for {fact_path} -> {resource}"
        )
    return found[0]


def options(fact_path: str, resource: str) -> list[Json]:
    """The codings of an entry whose code and display list alternatives (`a | b`). An entry
    without a display gives codings without one."""
    row = entry(fact_path, resource)
    codes = str(row["code"]).split(" | ")
    displays = str(row["display"]).split(" | ") if row["display"] else [None] * len(codes)
    return [
        {"system": row["code_system"], "code": code} | ({"display": display} if display else {})
        for code, display in zip(codes, displays, strict=True)
    ]


def coding(fact_path: str, resource: str) -> Json:
    """The single coding of an entry (system, code, display)."""
    [only] = options(fact_path, resource)
    return only


def questionnaire_canonical() -> str:
    q = questionnaire()
    canonical = f"{q['url']}|{q['version']}"
    if q["url"] != entry("intake.questionnaire_response", "QuestionnaireResponse")["code"]:
        raise LookupError("the Questionnaire url differs from mapping.yaml")
    return canonical


__all__ = [
    "ACVPU_ANSWERS",
    "BP_PANEL",
    "ENCOUNTER_CLASS_DISPLAY",
    "FLAG_CLINICAL",
    "LEVEL_TO_PRIORITY",
    "LOINC",
    "PARTICIPANT_DISPLAY",
    "SCALE_DISPLAY",
    "SPO2_PULSE_OXIMETRY",
    "SURVEY",
    "TASK_TEXT",
    "UCUM",
    "VITAL_SIGNS",
    "code_system",
    "coding",
    "entry",
    "mapping",
    "options",
    "questionnaire",
    "questionnaire_canonical",
]
