"""Build the synthetic TriageRecord fixtures for the write-back tests (M1-09).

    uv run python services/integration/tests/fixtures/writeback/generate.py

The facts are written by hand for three seeded personas (infra/seed/personas.yaml). The alerts
and the NEWS2 come from the real rules engine run on those facts, so the records match what
M1-01/M1-02 raise. Synthetic data only.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from careflow_contracts.generated.structured_facts_schema import StructuredFacts
from careflow_contracts.ids import stable_fact_id
from careflow_integration.fhir.writeback.record import News2Summary, TriageRecord
from careflow_rules import evaluate

HERE = Path(__file__).resolve().parent
DEVICE = "sim_vitals_01"
NURSE = "nurse_demo_01"
CLINIC = "clinic_demo"
CATEGORY = "https://careflow.example/fhir/CodeSystem/complaint-category"
SEX = "http://hl7.org/fhir/administrative-gender"
ACVPU = "https://careflow.example/fhir/CodeSystem/acvpu"


def at(minute: int, second: int = 0) -> str:
    return f"2026-09-26T09:{minute:02d}:{second:02d}Z"


class Facts:
    """Fact dicts with stable ids for one session."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.turn = 0

    def _fact(
        self,
        path: str,
        provenance: str,
        source: dict[str, Any],
        when: str,
        value: Any,
        **extra: Any,
    ) -> dict[str, Any]:
        fact_id = stable_fact_id(self.session_id, path)
        return {
            "fact_id": fact_id,
            "provenance": provenance,
            "source": source,
            "recorded_at": when,
            "value": value,
            **extra,
        }

    def said(self, path: str, value: Any, quote: str, minute: int) -> dict[str, Any]:
        self.turn += 1
        source = {
            "kind": "transcript_span",
            "turn_id": f"turn_{self.turn:02d}",
            "start": 0,
            "end": len(quote),
            "quote": quote,
        }
        return self._fact(path, "PATIENT_STATED", source, at(minute), value)

    def device(self, path: str, value: float, unit: str, minute: int) -> dict[str, Any]:
        source = {"kind": "device", "device_id": DEVICE, "reading_id": f"rd_{self.session_id}"}
        return self._fact(path, "DEVICE_MEASURED", source, at(minute), value, unit=unit)

    def nurse(self, path: str, value: Any, minute: int, **extra: Any) -> dict[str, Any]:
        return self._fact(
            path,
            "NURSE_CONFIRMED",
            {"kind": "nurse", "staff_user_id": NURSE},
            at(minute),
            value,
            **extra,
        )

    def ehr(self, path: str, value: Any, patient: str) -> dict[str, Any]:
        source = {"kind": "fhir", "resource_type": "Patient", "id": patient, "version_id": "1"}
        return self._fact(path, "EHR", source, at(0), value)

    def model(self, path: str, code: str, display: str, minute: int) -> dict[str, Any]:
        source = {
            "kind": "model",
            "model_id": "careflow_triage.complaint_category",
            "model_version": "0.1.0",
        }
        return self._fact(
            path,
            "MODEL_INFERRED",
            source,
            at(minute),
            {"system": CATEGORY, "code": code, "display": display},
        )


def patient(f: Facts, fhir_id: str, age: int, sex: str) -> dict[str, Any]:
    return {
        "fhir_patient_id": fhir_id,
        "age_years": f.ehr("patient.age_years", age, fhir_id),
        "sex": f.ehr(
            "patient.sex", {"system": SEX, "code": sex, "display": sex.capitalize()}, fhir_id
        ),
    }


def chest_pain() -> tuple[dict[str, Any], int, str]:
    f = Facts("ses_chest_pain")
    facts = {
        "patient": patient(f, "persona-chest-pain", 58, "male"),
        "chief_complaint": f.said(
            "chief_complaint",
            "Crushing chest pain for about an hour",
            "crushing chest pain for about an hour",
            1,
        ),
        "complaint_category": f.model("complaint_category", "chest_pain", "Chest pain", 1),
        "pain_present": f.said("pain_present", "yes", "crushing chest pain", 1),
        "pain_score": f.said("pain_score", 8, "about an eight", 2),
        "chest_pain": {
            "present": f.said("chest_pain.present", "yes", "chest pain", 1),
            "radiates": f.said("chest_pain.radiates", "yes", "it goes down my left arm", 2),
            "sweating": f.said("chest_pain.sweating", "yes", "I am sweating", 2),
            "short_of_breath": f.said(
                "chest_pain.short_of_breath", "no", "no, breathing is fine", 2
            ),
            "onset": f.said("chest_pain.onset", "PT1H", "about an hour", 1),
        },
        "vitals": {
            "heart_rate": f.device("vitals.heart_rate", 96, "/min", 3),
            "spo2": f.device("vitals.spo2", 97, "%", 3),
            "temperature_c": f.device("vitals.temperature_c", 36.8, "Cel", 3),
            "systolic_bp": f.device("vitals.systolic_bp", 158, "mm[Hg]", 3),
            "diastolic_bp": f.device("vitals.diastolic_bp", 94, "mm[Hg]", 3),
            "respiratory_rate": f.nurse("vitals.respiratory_rate", 18, 5, unit="/min"),
            "acvpu": f.nurse("vitals.acvpu", {"system": ACVPU, "code": "A", "display": "Alert"}, 5),
            "on_supplemental_o2": f.nurse("vitals.on_supplemental_o2", "no", 5),
        },
    }
    return facts, 2, "CONFIRM"


def sepsis() -> tuple[dict[str, Any], int, str]:
    f = Facts("ses_sepsis")
    facts = {
        "patient": patient(f, "persona-sepsis", 66, "male"),
        "chief_complaint": f.said(
            "chief_complaint",
            "Fever and shivering with a cough",
            "fever and shivering with a cough",
            1,
        ),
        "complaint_category": f.model("complaint_category", "fever", "Fever", 1),
        "symptoms": [
            {
                "name": f.said("symptoms.0.name", "productive_cough", "a cough with phlegm", 1),
                "onset": f.said("symptoms.0.onset", "P3D", "three days", 2),
                "severity": "moderate",
            }
        ],
        "infection": {
            "fever_reported": f.said("infection.fever_reported", "yes", "fever", 1),
            "rigors": f.said("infection.rigors", "yes", "shivering", 1),
        },
        "vitals": {
            "heart_rate": f.device("vitals.heart_rate", 125, "/min", 3),
            "temperature_c": f.device("vitals.temperature_c", 39.4, "Cel", 3),
            "spo2": f.device("vitals.spo2", 93, "%", 3),
        },
    }
    return facts, 2, "CONFIRM"


def routine() -> tuple[dict[str, Any], int, str]:
    f = Facts("ses_routine")
    facts = {
        "patient": patient(f, "persona-routine", 34, "female"),
        "chief_complaint": f.said(
            "chief_complaint",
            "Sore throat for five days, getting better",
            "sore throat for five days, getting better",
            1,
        ),
        "complaint_category": f.model(
            "complaint_category", "eye_ent", "Eye, ear, nose or throat", 1
        ),
        "symptoms": [
            {
                "name": f.said("symptoms.0.name", "sore_throat", "sore throat", 1),
                "onset": f.said("symptoms.0.onset", "P5D", "five days", 1),
                "severity": "mild",
            }
        ],
        "pain_present": f.said("pain_present", "yes", "sore", 1),
        "pain_score": f.said("pain_score", 3, "a three", 2),
        "vitals": {
            "heart_rate": f.device("vitals.heart_rate", 72, "/min", 3),
            "spo2": f.device("vitals.spo2", 99, "%", 3),
            "temperature_c": f.device("vitals.temperature_c", 37.0, "Cel", 3),
            "systolic_bp": f.device("vitals.systolic_bp", 118, "mm[Hg]", 3),
            "diastolic_bp": f.device("vitals.diastolic_bp", 76, "mm[Hg]", 3),
        },
    }
    return facts, 4, "OVERRIDE"


def record(name: str, persona: str, build: Any) -> TriageRecord:
    session_id = f"ses_{name}"
    body, level, action = build()
    facts = StructuredFacts.model_validate(
        {
            "session_id": session_id,
            "clinic_id": CLINIC,
            "language": "en",
            **body,
            "extraction_status": "complete",
        }
    )
    arrived = datetime.fromisoformat(at(0))
    result = evaluate(facts, datetime.fromisoformat(at(5)), arrived_at=arrived)
    news2 = None
    if result.news2 is not None:
        news2 = News2Summary(
            total=result.news2.total,
            partial=result.news2.partial,
            missing=result.news2.missing,
            scored_at=datetime.fromisoformat(at(5)),
        )
    decision: dict[str, Any] = {
        "decision_id": f"dec_{name}",
        "session_id": session_id,
        "suggestion_id": f"sug_{name}",
        "action": action,
        "final_level": level,
        "scale": "KTAS",
        "nurse_user_id": NURSE,
        "acknowledged_alert_ids": [a.alert_id for a in result.alerts],
        "decided_at": at(6),
    }
    if action == "OVERRIDE":
        decision["override_reason_code"] = "clinical_judgement"
    return TriageRecord.model_validate(
        {
            "clinic_id": CLINIC,
            "session_id": session_id,
            "patient_ref": f"Patient/{persona}",
            "arrived_at": at(0),
            "decision": decision,
            "facts": facts,
            "alerts": result.alerts,
            "news2": news2,
        }
    )


def main() -> None:
    for name, persona, build in (
        ("chest_pain", "persona-chest-pain", chest_pain),
        ("sepsis", "persona-sepsis", sepsis),
        ("routine", "persona-routine", routine),
    ):
        text = record(name, persona, build).model_dump_json(indent=1, exclude_none=True)
        (HERE / f"{name}.json").write_text(text + "\n", encoding="utf-8")
        print(name, "written")


if __name__ == "__main__":
    main()
