"""FHIR R4 mapping table, intake Questionnaire and example resources (offline structure tests)."""

from __future__ import annotations

import importlib
import json
import types
from pathlib import Path
from typing import Any, Union, get_args, get_origin

import pytest
import yaml
from pydantic import BaseModel

from careflow_contracts import FactBase, FactKey, StructuredFacts
from careflow_contracts.testing import CONTRACTS_DIR

FHIR = CONTRACTS_DIR / "fhir"
MAPPING: dict[str, Any] = yaml.safe_load((FHIR / "mapping.yaml").read_text(encoding="utf-8"))
MAPPING_MD = (FHIR / "mapping.md").read_text(encoding="utf-8")
QUESTIONNAIRE: dict[str, Any] = json.loads(
    (FHIR / "questionnaire-intake.json").read_text(encoding="utf-8")
)
EXAMPLES = sorted((FHIR / "examples").glob("*.json"))
PROVENANCE_SOURCE = "https://careflow.example/fhir/CodeSystem/provenance-source"
FIELDS = (
    *("fact_path", "direction", "resource", "element"),
    *("code_system", "code", "display", "unit", "verified", "notes"),
)


def unwrap(annotation: Any) -> list[Any]:
    """The concrete classes inside Optional / Union / Annotated / list / type aliases."""
    value = getattr(annotation, "__value__", None)  # PEP 695 `type` alias
    if value is not None:
        return unwrap(value)
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        return [t for arg in get_args(annotation) for t in unwrap(arg)]
    if origin is not None and get_args(annotation):
        if origin is list:
            return [("list", t) for t in unwrap(get_args(annotation)[0])]
        return unwrap(get_args(annotation)[0])  # Annotated[...]
    return [annotation]


def model_leaf_paths(model: type[BaseModel] = StructuredFacts, prefix: str = "") -> set[str]:
    """Walk the generated StructuredFacts model: facts, and list-valued paths, are leaves."""
    leaves: set[str] = set()
    for name, field in model.model_fields.items():
        path = f"{prefix}{name}"
        for kind in unwrap(field.annotation):
            if isinstance(kind, tuple):  # a list (of facts, or of symptoms) is itself a leaf
                leaves.add(path)
            elif isinstance(kind, type) and issubclass(kind, FactBase):
                leaves.add(path)
            elif isinstance(kind, type) and issubclass(kind, BaseModel):
                leaves |= model_leaf_paths(kind, f"{path}.")
    return leaves


def mapped_paths() -> set[str]:
    return {entry["fact_path"] for entry in MAPPING["entries"]}


def test_every_leaf_fact_is_mapped_or_explicitly_not_mapped() -> None:
    leaves = model_leaf_paths()
    assert leaves == {key.value for key in FactKey}  # the walk agrees with the contract enum
    not_mapped = {item["fact_path"] for item in MAPPING["not_mapped"]}
    assert all(item.get("reason") for item in MAPPING["not_mapped"])
    assert leaves - mapped_paths() - not_mapped == set()


def test_entries_have_every_field() -> None:
    for entry in MAPPING["entries"]:
        assert tuple(entry) == FIELDS, entry["fact_path"]
        assert entry["direction"] in {"read", "write"}
        assert isinstance(entry["verified"], bool)
        assert entry["notes"]


@pytest.mark.parametrize(
    ("path", "code", "unit"),
    [
        ("vitals.heart_rate", "8867-4", "/min"),
        ("vitals.respiratory_rate", "9279-1", "/min"),
        ("vitals.spo2", "2708-6", "%"),
        ("vitals.temperature_c", "8310-5", "Cel"),
        ("vitals.systolic_bp", "8480-6", "mm[Hg]"),
        ("vitals.diastolic_bp", "8462-4", "mm[Hg]"),
        ("pain_score", "72514-3", "{score}"),
    ],
)
def test_vital_sign_codes_and_units(path: str, code: str, unit: str) -> None:
    [entry] = [e for e in MAPPING["entries"] if e["fact_path"] == path and e["code"] == code]
    assert entry["code_system"] == "http://loinc.org"
    assert entry["unit"] == unit
    assert entry["verified"] is True


def test_spo2_has_the_second_coding_and_bp_the_panel() -> None:
    entries = MAPPING["entries"]
    notes = {e["fact_path"]: e["notes"] for e in entries if e["resource"] == "Observation"}
    assert "59408-5" in notes["vitals.spo2"]
    assert "85354-9" in notes["vitals.systolic_bp"]


def test_unverified_entries_are_open_questions() -> None:
    open_questions = MAPPING_MD.split("## Open questions", 1)[1].split("\n## ", 1)[0]
    for entry in MAPPING["entries"]:
        if not entry["verified"]:
            assert f"`{entry['fact_path']}`" in open_questions, entry["fact_path"]


def test_mapping_md_lists_every_entry_and_labels_the_convention() -> None:
    for entry in MAPPING["entries"]:
        assert f"`{entry['fact_path']}`" in MAPPING_MD
    assert "CareFlow's own convention" in MAPPING_MD
    assert "R4 only for now" in MAPPING_MD


def fhir_class(resource_type: str) -> type[BaseModel]:
    module = importlib.import_module(f"fhir.resources.R4B.{resource_type.lower()}")
    return getattr(module, resource_type)


@pytest.mark.parametrize(
    "path", [*EXAMPLES, FHIR / "questionnaire-intake.json"], ids=lambda p: p.name
)
def test_example_parses_with_fhir_resources(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    fhir_class(data["resourceType"]).model_validate(data)


def test_required_examples_exist() -> None:
    names = {p.stem for p in EXAMPLES}
    required = {
        "encounter",
        "observation-heart-rate",
        "observation-respiratory-rate",
        "observation-spo2",
        "observation-body-temperature",
        "observation-blood-pressure",
        "observation-pain-score",
        "observation-triage-level",
        "questionnaire-response",
        "flag",
        "task-ecg",
        "communication",
        "composition-bundle",
        "provenance",
    }
    assert required <= names


@pytest.mark.parametrize(
    "path", [p for p in EXAMPLES if not p.stem.startswith("read-")], ids=lambda p: p.name
)
def test_every_written_resource_carries_a_provenance_tag(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    resources = [data] + [entry["resource"] for entry in data.get("entry", [])]
    for resource in resources:  # EHR resources inside the briefing document are tagged EHR
        tags = resource.get("meta", {}).get("tag", [])
        assert tags, resource["id"]
        assert all(tag["system"] == PROVENANCE_SOURCE for tag in tags), resource["id"]


def test_document_bundle_has_the_composition_first() -> None:
    bundle = json.loads((FHIR / "examples" / "composition-bundle.json").read_text(encoding="utf-8"))
    assert bundle["type"] == "document"
    first = bundle["entry"][0]["resource"]
    assert first["resourceType"] == "Composition"
    assert first["type"]["coding"][0] == {
        "system": "http://loinc.org",
        "code": "54094-8",
        "display": "Emergency Department Triage Note",
    }


def test_patient_stated_allergy_is_unconfirmed() -> None:
    allergy = json.loads(
        (FHIR / "examples" / "allergy-intolerance-patient-stated.json").read_text(encoding="utf-8")
    )
    assert allergy["verificationStatus"]["coding"][0]["code"] == "unconfirmed"


def questionnaire_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        out.append(item)
        out.extend(questionnaire_items(item.get("item", [])))
    return out


def test_questionnaire_link_ids_are_fact_paths() -> None:
    groups = {key.value.split(".", 1)[0] for key in FactKey if "." in key.value}
    symptom_fields = {f"symptoms.{name}" for name in ("name", "onset", "severity")}
    valid = {key.value for key in FactKey} | groups | symptom_fields
    link_ids = [item["linkId"] for item in questionnaire_items(QUESTIONNAIRE["item"])]
    assert len(link_ids) == len(set(link_ids))
    assert set(link_ids) <= valid, set(link_ids) - valid
    for required in ("chief_complaint", "pain_score", "fast.face_droop", "chest_pain.radiates"):
        assert required in link_ids
    assert "child_danger.hard_to_wake" in link_ids


def test_questionnaire_header_types_and_enable_when() -> None:
    assert QUESTIONNAIRE["url"] == "https://careflow.example/fhir/Questionnaire/careflow-intake"
    assert (QUESTIONNAIRE["version"], QUESTIONNAIRE["status"]) == ("0.1.0", "draft")
    items = {item["linkId"]: item for item in questionnaire_items(QUESTIONNAIRE["item"])}
    assert {item["type"] for item in items.values()} == {"group", "string", "integer", "choice"}
    extensions = items["pain_score"]["extension"]
    pain = {ext["url"].rsplit("/", 1)[-1]: ext["valueInteger"] for ext in extensions}
    assert pain == {"minValue": 0, "maxValue": 10}
    assert items["chest_pain.radiates"]["enableWhen"][0]["question"] == "chest_pain.present"
    assert items["child_danger"]["enableWhen"][0]["question"] == "patient.age_years"
    tri = [opt["valueCoding"]["code"] for opt in items["fast.face_droop"]["answerOption"]]
    assert tri == ["yes", "no", "unknown"]


def test_questionnaire_response_uses_the_canonical_url_and_real_link_ids() -> None:
    response = json.loads(
        (FHIR / "examples" / "questionnaire-response.json").read_text(encoding="utf-8")
    )
    assert response["questionnaire"].startswith(QUESTIONNAIRE["url"])
    link_ids = {item["linkId"] for item in questionnaire_items(QUESTIONNAIRE["item"])}
    assert {item["linkId"] for item in questionnaire_items(response["item"])} <= link_ids
