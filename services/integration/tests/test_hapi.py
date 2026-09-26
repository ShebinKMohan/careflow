"""The seeded HAPI FHIR server, read end to end: seed bundle → HAPI → search → mapper (M1-08).

    make up && uv run pytest services/integration -m hapi

Skipped when HAPI does not answer (conftest). Expectations come from the persona bundles in
infra/seed/personas/, and from HAPI itself for a Synthea patient with allergies.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

import pytest

from careflow_contracts.generated.ehr_fetch_schema import EhrFetch, EhrStatus, PatientIdentifier
from careflow_contracts.generated.fact_schema import FhirSource
from careflow_integration.adapters.loader import REPO, load_adapter
from careflow_integration.fhir.client import FhirClient
from careflow_integration.fhir.reader import FhirEhrReader
from careflow_integration.fhir.record import personas

pytestmark = pytest.mark.hapi

BUNDLES = REPO / "infra" / "seed" / "personas"
PERSONAS = personas()


def display(concept: dict[str, Any]) -> str:
    return concept.get("text") or concept["coding"][0]["display"]


def is_active(resource: dict[str, Any]) -> bool:
    codes = {c.get("code") for c in resource.get("clinicalStatus", {}).get("coding", [])}
    return "active" in codes or resource.get("status") == "active"


def seeded(persona_id: str) -> dict[str, list[str]]:
    """The active allergies, conditions and medications in a persona's seed bundle."""
    bundle = json.loads((BUNDLES / f"{persona_id}.json").read_text(encoding="utf-8"))
    fields = {
        "AllergyIntolerance": ("allergies", "code"),
        "Condition": ("conditions", "code"),
        "MedicationStatement": ("medications", "medicationCodeableConcept"),
        "MedicationRequest": ("medications", "medicationCodeableConcept"),
    }
    found: dict[str, list[str]] = {"allergies": [], "conditions": [], "medications": []}
    for entry in bundle["entry"]:
        resource = entry["resource"]
        if resource["resourceType"] in fields and is_active(resource):
            name, concept = fields[resource["resourceType"]]
            found[name].append(display(resource[concept]))
    return {name: sorted(values) for name, values in found.items()}


def read_back(fetch: EhrFetch) -> dict[str, list[str]]:
    assert fetch.context is not None
    return {
        "allergies": sorted(f.value.display or "" for f in fetch.context.allergies or []),
        "conditions": sorted(f.value.display or "" for f in fetch.context.conditions or []),
        "medications": sorted(f.value.display or "" for f in fetch.context.medications or []),
    }


def years_since(born: str, today: date) -> int:
    birth = date.fromisoformat(born)
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


@pytest.mark.parametrize("persona_id", sorted(PERSONAS))
async def test_each_persona_reads_back_as_seeded(persona_id: str) -> None:
    persona = PERSONAS[persona_id]
    adapter = load_adapter()
    identifier = PatientIdentifier(
        system=adapter.identifier_systems["keypad"], value=persona["keypad_id"]
    )
    fetch = await FhirEhrReader(adapter).fetch_context(identifier, "demo")
    assert fetch.status is EhrStatus.ok, fetch.errors
    assert fetch.patient_ref == f"Patient/{persona['fhir_patient_id']}"
    assert read_back(fetch) == seeded(persona_id)
    assert fetch.patient is not None and fetch.patient.sex is not None
    assert fetch.patient.sex.value.code == persona["sex"]
    assert fetch.patient.age_years is not None
    assert fetch.patient.age_years.value == years_since(
        persona["birth_date"], datetime.now(UTC).date()
    )


async def test_a_synthea_patient_is_found_by_mr_number_with_its_allergies() -> None:
    """The personas have no allergies, so a Synthea patient checks allergy mapping and the MR
    identifier system in the adapter."""
    adapter = load_adapter()
    async with FhirClient(adapter.fhir_base_url) as client:
        first = (await client.search("AllergyIntolerance", {"clinical-status": "active"}))[0]
        pid = first["patient"]["reference"].removeprefix("Patient/")
        patient = await client.read("Patient", pid)
        allergies = await client.search(
            "AllergyIntolerance", {"patient": pid, "clinical-status": "active"}
        )
    mr = adapter.identifier_systems["mr"]
    [value] = [i["value"] for i in patient["identifier"] if i.get("system") == mr]
    fetch = await FhirEhrReader(adapter).fetch_context(
        PatientIdentifier(system=mr, value=value), "demo"
    )
    assert fetch.status is EhrStatus.ok and fetch.context is not None
    got = sorted(
        (f.value.display, f.criticality.value if f.criticality else None)
        for f in fetch.context.allergies or []
    )
    assert got == sorted((display(a["code"]), a.get("criticality")) for a in allergies)
    for fact in fetch.context.allergies or []:
        assert fact.provenance.value == "EHR" and isinstance(fact.source, FhirSource)
        assert fact.source.resource_type == "AllergyIntolerance"
