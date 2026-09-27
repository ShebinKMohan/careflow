"""Schemas are valid 2020-12, fixtures behave, and Pydantic agrees with jsonschema."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from careflow_contracts import StructuredFacts
from careflow_contracts.testing import (
    MODEL_FOR_SCHEMA,
    fixture_paths,
    load_fixture,
    load_schema,
    schema_errors,
    schema_files,
    schema_name_of,
)

PERSONAS = ("chest_pain", "stroke", "child_drowsy", "sepsis_partial", "routine")
INVALID = {
    "pain_score_11.structured-facts.json",
    "override_without_reason.nurse-decision.json",
    "alert_empty_evidence.alert.json",
    "patient_stated_fhir_source.structured-facts.json",
    "four_reasons.urgency-suggestion.json",
    "free_form_key.extraction-output.json",
    "unknown_status.ehr-fetch.json",
}


def model_errors(schema_name: str, raw: str) -> str | None:
    try:
        MODEL_FOR_SCHEMA[schema_name].model_validate_json(raw)
    except ValidationError as exc:
        return str(exc)
    return None


def test_the_schema_files_exist() -> None:
    assert set(schema_files()) == {
        "fact",
        "structured-facts",
        "urgency-suggestion",
        "alert",
        "nurse-decision",
        "extraction-output",
        "ehr-fetch",
        "retrieved-chunk",  # M2-08
        "ask-agent",  # M2-09
    }


@pytest.mark.parametrize("name", sorted(schema_files()))
def test_schema_is_valid_draft_2020_12(name: str) -> None:
    Draft202012Validator.check_schema(load_schema(name))


def test_fixture_sets_are_complete() -> None:
    valid = {p.name for p in fixture_paths("valid")}
    for persona in PERSONAS:
        assert f"{persona}.structured-facts.json" in valid
        assert f"{persona}.urgency-suggestion.json" in valid
        assert f"{persona}.nurse-decision.json" in valid
    assert {p.name for p in fixture_paths("invalid")} == INVALID


@pytest.mark.parametrize("path", fixture_paths("valid"), ids=lambda p: p.name)
def test_valid_fixture_passes_schema_and_model(path: Path) -> None:
    name = schema_name_of(path)
    raw = path.read_text(encoding="utf-8")
    assert schema_errors(name, json.loads(raw)) == []
    assert model_errors(name, raw) is None


@pytest.mark.parametrize("path", fixture_paths("invalid"), ids=lambda p: p.name)
def test_invalid_fixture_fails_schema_or_model(path: Path) -> None:
    name = schema_name_of(path)
    raw = path.read_text(encoding="utf-8")
    assert schema_errors(name, json.loads(raw)) or model_errors(name, raw)


@pytest.mark.parametrize("path", fixture_paths("valid"), ids=lambda p: p.name)
def test_model_round_trips_the_fixture(path: Path) -> None:
    raw = path.read_text(encoding="utf-8")
    model = MODEL_FOR_SCHEMA[schema_name_of(path)].model_validate_json(raw)
    assert model.model_dump(mode="json", exclude_none=True) == json.loads(raw)


def routine() -> dict[str, Any]:
    return copy.deepcopy(load_fixture("valid/routine.structured-facts.json"))  # type: ignore[return-value]


def both_reject(doc: dict[str, Any]) -> bool:
    return bool(schema_errors("structured-facts", doc)) and bool(
        model_errors("structured-facts", json.dumps(doc))
    )


def test_unknown_field_on_a_fact_is_rejected() -> None:
    doc = routine()
    doc["pain_score"]["note"] = "free text"
    assert both_reject(doc)


def test_unknown_top_level_field_is_rejected() -> None:
    doc = routine()
    doc["diagnosis"] = "x"
    assert both_reject(doc)


def test_vital_units_are_fixed() -> None:
    doc = routine()
    doc["vitals"]["temperature_c"]["unit"] = "[degF]"
    assert both_reject(doc)


def test_spo2_cannot_exceed_100() -> None:
    doc = routine()
    doc["vitals"]["spo2"]["value"] = 101
    assert both_reject(doc)


def test_acvpu_codes_are_fixed() -> None:
    doc = routine()
    source = {"kind": "nurse", "staff_user_id": "nurse_demo_01"}
    doc["vitals"]["acvpu"] = {
        "fact_id": "f_01K62QZ0000000000000000ZZZ",
        "provenance": "NURSE_CONFIRMED",
        "source": source,
        "recorded_at": "2026-09-26T09:05:00Z",
        "value": {"system": "https://careflow.example/fhir/CodeSystem/acvpu", "code": "V"},
    }
    assert schema_errors("structured-facts", doc) == []
    assert model_errors("structured-facts", json.dumps(doc)) is None
    doc["vitals"]["acvpu"]["value"]["code"] = "X"
    assert both_reject(doc)


def test_naive_datetimes_are_rejected() -> None:
    doc = routine()
    doc["pain_score"]["recorded_at"] = "2026-09-26T09:00:00"
    assert both_reject(doc)


def test_level_description_says_1_is_most_urgent() -> None:
    level = load_schema("urgency-suggestion")["$defs"]["UrgencyLevel"]  # type: ignore[index]
    assert "1 = most urgent" in level["description"]  # type: ignore[index]


def test_structured_facts_is_exported() -> None:
    assert StructuredFacts.model_json_schema()["title"] == "StructuredFacts"
