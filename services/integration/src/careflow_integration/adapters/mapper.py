"""Map FHIR R4 resources to CareFlow EHR facts (M1-08).

Resources are parsed with the `fhir.resources` R4B models (the pinned 8.x package has no plain R4
models; R4B reads these R4 resources unchanged, ADR-023). A resource that does not parse is skipped
and logged, never raised. Every fact has provenance EHR and a FHIR source (resource type, id,
version).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, Final

from fhir.resources.R4B.allergyintolerance import AllergyIntolerance
from fhir.resources.R4B.appointment import Appointment
from fhir.resources.R4B.condition import Condition
from fhir.resources.R4B.medicationrequest import MedicationRequest
from fhir.resources.R4B.medicationstatement import MedicationStatement
from fhir.resources.R4B.patient import Patient as FhirPatient
from pydantic import BaseModel, ValidationError

from careflow_contracts.generated.ehr_fetch_schema import EhrAppointment
from careflow_contracts.generated.fact_schema import CodeFact
from careflow_contracts.generated.structured_facts_schema import AllergyFact, Patient
from careflow_contracts.ids import stable_fact_id
from careflow_integration.adapters.loader import HospitalAdapter

logger = logging.getLogger("careflow_integration.fhir")

type Json = dict[str, Any]

UNCODED_SYSTEM: Final = "urn:careflow:uncoded"
GENDER_SYSTEM: Final = "http://hl7.org/fhir/administrative-gender"
CHILD_MONTHS: Final = 60  # an exact age in months under 5 years, for the NG143 thresholds
MODELS: Final[dict[str, type[BaseModel]]] = {
    "Patient": FhirPatient,
    "AllergyIntolerance": AllergyIntolerance,
    "Condition": Condition,
    "MedicationStatement": MedicationStatement,
    "MedicationRequest": MedicationRequest,
    "Appointment": Appointment,
}
_STEP = re.compile(r"^(?P<name>[A-Za-z]+)(?:\[(?P<index>\d+)\])?$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")


def parse(resource: Json) -> Json | None:
    """The resource validated against its R4B model, or None when it does not parse (logged)."""
    resource_type = str(resource.get("resourceType", ""))
    model = MODELS.get(resource_type)
    if model is None:
        logger.warning(
            "fhir.resource_skipped", extra={"resource_type": resource_type, "reason": "unexpected"}
        )
        return None
    try:
        parsed = model.model_validate(resource)
    except ValidationError:
        logger.warning(
            "fhir.resource_skipped", extra={"resource_type": resource_type, "reason": "unparseable"}
        )
        return None
    return parsed.model_dump(mode="json", exclude_none=True)


def value_at(node: Any, path: str) -> Any:
    """`text` or `coding[0].display` style paths into parsed JSON; None when absent."""
    for step in path.split("."):
        match = _STEP.match(step)
        if match is None or not isinstance(node, dict):
            return None
        node = node.get(match["name"])
        if match["index"] is not None:
            index = int(match["index"])
            node = node[index] if isinstance(node, list) and index < len(node) else None
    return node


def coding(concept: Json | None, adapter: HospitalAdapter) -> Json | None:
    """A contract Coding for a CodeableConcept: its first usable coding, with the display the
    adapter prefers. Text without a coding keeps the text under CareFlow's uncoded system."""
    if not concept:
        return None
    display = next(
        (str(text)[:256] for path in adapter.display_paths if (text := value_at(concept, path))),
        None,
    )
    for item in concept.get("coding", []):
        system, code = item.get("system"), item.get("code")
        if system and code and len(str(code)) <= 64:
            return {"system": system, "code": str(code), "display": display or item.get("display")}
    if display:
        return {"system": UNCODED_SYSTEM, "code": "uncoded", "display": display}
    return None


def source(resource: Json) -> Json:
    version = str(resource.get("meta", {}).get("versionId", ""))
    body: Json = {"kind": "fhir", "resource_type": resource["resourceType"], "id": resource["id"]}
    if _IDENTIFIER.match(version):
        body["version_id"] = version
    return body


def fact(
    resource: Json, value: Any, recorded_at: datetime, *, part: str = "", **extra: Any
) -> Json:
    version = str(resource.get("meta", {}).get("versionId", ""))
    return {
        "fact_id": stable_fact_id(resource["resourceType"], resource["id"], version, part),
        "provenance": "EHR",
        "source": source(resource),
        "recorded_at": recorded_at,
        "value": value,
        **extra,
    }


def _build[M: BaseModel](model: type[M], body: Json) -> M | None:
    try:
        return model.model_validate(body)
    except ValidationError:
        logger.warning(
            "fhir.fact_skipped", extra={"resource_type": body["source"]["resource_type"]}
        )
        return None


def allergies(
    resources: Sequence[Json], adapter: HospitalAdapter, at: datetime
) -> list[AllergyFact]:
    facts = []
    for resource in resources:
        value = coding(resource.get("code"), adapter)
        if value is None:
            continue
        extra = {"criticality": c} if (c := resource.get("criticality")) else {}
        if (built := _build(AllergyFact, fact(resource, value, at, **extra))) is not None:
            facts.append(built)
    return facts


def _in_categories(resource: Json, adapter: HospitalAdapter) -> bool:
    codes = {
        item.get("code")
        for category in resource.get("category", [])
        for item in category.get("coding", [])
    }
    return not codes or bool(codes & set(adapter.condition_categories))


def conditions(resources: Sequence[Json], adapter: HospitalAdapter, at: datetime) -> list[CodeFact]:
    facts = []
    for resource in resources:
        if not _in_categories(resource, adapter):
            continue
        value = coding(resource.get("code"), adapter)
        if value is not None and (built := _build(CodeFact, fact(resource, value, at))) is not None:
            facts.append(built)
    return facts


def medications(
    resources: Sequence[Json], adapter: HospitalAdapter, at: datetime
) -> list[CodeFact]:
    """Coded medications. A medication given only as a reference to a Medication resource is
    skipped (not resolved here)."""
    facts = []
    for resource in resources:
        value = coding(resource.get("medicationCodeableConcept"), adapter)
        if value is not None and (built := _build(CodeFact, fact(resource, value, at))) is not None:
            facts.append(built)
    return facts


def appointments(resources: Sequence[Json], adapter: HospitalAdapter) -> list[EhrAppointment]:
    found = []
    for resource in resources:
        if resource.get("status") not in adapter.appointment_statuses:
            continue
        body: Json = {
            "appointment_id": resource["id"],
            "status": resource["status"],
            "source": source(resource),
        }
        if start := resource.get("start"):
            body["start"] = start
        if description := resource.get("description"):
            body["description"] = str(description)[:500]
        try:
            found.append(EhrAppointment.model_validate(body))
        except ValidationError:
            logger.warning("fhir.fact_skipped", extra={"resource_type": "Appointment"})
    return found


def _months(born: date, today: date) -> int:
    return (today.year - born.year) * 12 + today.month - born.month - (today.day < born.day)


def patient(resource: Json, at: datetime) -> Patient:
    """Demographics: the FHIR id, age in years (and exact months under 5 years), and sex."""
    body: Json = {"fhir_patient_id": resource["id"]}
    if born := resource.get("birthDate"):
        months = _months(date.fromisoformat(str(born)[:10]), at.date())
        if months >= 0:
            body["age_years"] = fact(resource, months // 12, at, part="age_years")
            if months < CHILD_MONTHS:
                body["age_months"] = fact(resource, months, at, part="age_months")
    if gender := resource.get("gender"):
        value = {"system": GENDER_SYSTEM, "code": gender, "display": str(gender).capitalize()}
        body["sex"] = fact(resource, value, at, part="sex")
    return Patient.model_validate(body)


__all__ = [
    "GENDER_SYSTEM",
    "UNCODED_SYSTEM",
    "allergies",
    "appointments",
    "coding",
    "conditions",
    "medications",
    "parse",
    "patient",
    "value_at",
]
