"""Offline checks of the seed personas (infra/seed) and the FHIR loader's safety guard."""

from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from careflow_contracts import StructuredFacts
from careflow_contracts.testing import load_model

SEED = Path(__file__).resolve().parents[2] / "infra" / "seed"
PERSONAS: dict[str, Any] = yaml.safe_load((SEED / "personas.yaml").read_text(encoding="utf-8"))
BY_ID = {p["id"]: p for p in PERSONAS["personas"]}
KEYPAD_SYSTEM = "https://careflow.example/fhir/sid/keypad-id"


def bundle(persona_id: str) -> dict[str, Any]:
    text = (SEED / "personas" / f"{persona_id}.json").read_text(encoding="utf-8")
    return json.loads(text.replace("__TODAY__", "2026-09-26"))


def resources(persona_id: str) -> list[dict[str, Any]]:
    return [entry["resource"] for entry in bundle(persona_id)["entry"]]


def loader() -> ModuleType:
    spec = importlib.util.spec_from_file_location("load_fhir", SEED / "load_fhir.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_seven_personas() -> None:
    assert list(BY_ID) == [
        "chest-pain",
        "stroke",
        "child-drowsy",
        "sepsis",
        "routine",
        "qr-phone",
        "no-show",
    ]
    assert [p["keypad_id"] for p in PERSONAS["personas"]] == [f"10000{i}" for i in range(1, 8)]
    for persona in PERSONAS["personas"]:
        assert set(persona) >= {
            "id",
            "keypad_id",
            "birth_date",
            "scenario",
            "language",
            "expected_rule",
        }
        assert persona["fhir_patient_id"] == f"persona-{persona['id']}"


@pytest.mark.parametrize("persona_id", list(BY_ID))
def test_bundles_are_idempotent_puts_that_parse(persona_id: str) -> None:
    doc = bundle(persona_id)
    assert doc["type"] == "transaction"
    for entry in doc["entry"]:
        resource = entry["resource"]
        assert entry["request"] == {
            "method": "PUT",
            "url": f"{resource['resourceType']}/{resource['id']}",
        }
        module = importlib.import_module(f"fhir.resources.R4B.{resource['resourceType'].lower()}")
        getattr(module, resource["resourceType"]).model_validate(resource)


@pytest.mark.parametrize("persona_id", list(BY_ID))
def test_patient_matches_personas_yaml(persona_id: str) -> None:
    [patient] = [r for r in resources(persona_id) if r["resourceType"] == "Patient"]
    persona = BY_ID[persona_id]
    assert patient["id"] == persona["fhir_patient_id"]
    assert patient["identifier"] == [{"system": KEYPAD_SYSTEM, "value": persona["keypad_id"]}]
    assert (patient["birthDate"], patient["gender"]) == (persona["birth_date"], persona["sex"])


def test_scenario_resources() -> None:
    stroke_meds = [r for r in resources("stroke") if r["resourceType"] == "MedicationStatement"]
    assert stroke_meds[0]["medicationCodeableConcept"]["coding"][0]["display"] == "warfarin"
    assert any(r["resourceType"] == "RelatedPerson" for r in resources("child-drowsy"))
    for persona_id in ("routine", "no-show"):
        [appointment] = [r for r in resources(persona_id) if r["resourceType"] == "Appointment"]
        assert appointment["status"] == "booked"
        raw = (SEED / "personas" / f"{persona_id}.json").read_text(encoding="utf-8")
        assert "__TODAY__" in raw  # the loader books it for the day it runs


@pytest.mark.parametrize(
    ("fixture", "persona_id"),
    [("chest_pain", "chest-pain"), ("stroke", "stroke"), ("child_drowsy", "child-drowsy"),
     ("sepsis_partial", "sepsis"), ("routine", "routine")],
)  # fmt: skip
def test_contract_fixtures_point_at_the_personas(fixture: str, persona_id: str) -> None:
    facts = load_model(f"valid/{fixture}.structured-facts.json", StructuredFacts)
    assert facts.patient is not None
    assert facts.patient.fhir_patient_id == BY_ID[persona_id]["fhir_patient_id"]


@pytest.mark.parametrize(
    "url", ["https://hapi.fhir.org/baseR4", "http://10.0.0.5:8080/fhir", "http://example.org/fhir"]
)
def test_loader_refuses_non_local_servers(url: str) -> None:
    with pytest.raises(SystemExit, match="refusing non-local"):
        loader().check_local(url)


def test_loader_accepts_localhost() -> None:
    loader().check_local("http://localhost:8080/fhir/")
    loader().check_local("http://127.0.0.1:8080/fhir/")
