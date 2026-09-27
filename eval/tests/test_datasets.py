"""Dataset admission and provenance tests; these never invoke a provider."""

from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml

import careflow_eval.datasets as datasets_module
from careflow_contracts.generated.fact_schema import Provenance, TranscriptSpanSource
from careflow_contracts.generated.structured_facts_schema import FactKey, TurnRole
from careflow_eval.datasets import (
    DATASETS_DIR,
    NOW,
    SCHEMA_PATH,
    DatasetError,
    content_hash,
    context_entries,
    expected_entries,
    load_dataset,
    review_content,
    transcript,
)
from careflow_eval.models import Scenario
from careflow_eval.validation import validate_scenario
from careflow_intake.facts import current_entries


@pytest.fixture
def raw_case() -> dict[str, Any]:
    return {
        "synthetic": True,
        "id": "synthetic-test",
        "category": "routine",
        "language": "en",
        "description": "An invented test case with no identifying data.",
        "patient": {"age_years": 30, "sex": "female"},
        "turns": [
            {"question_key": "chief_complaint", "patient_text": "My elbow hurts."},
            {"question_key": "pain_score", "patient_text": "Three out of ten."},
            {"question_key": "chest_pain.present", "patient_text": "No."},
        ],
        "device_vitals": {},
        "expected": {
            "facts": {
                "chief_complaint": "My elbow hurts.",
                "pain_score": 3,
                "chest_pain.present": "no",
            },
            "alerts": [],
            "level": 5,
            "level_rationale": "Illustrative synthetic minor complaint.",
        },
        "generated_by": {"tool": "coding-agent", "model": "GPT-6", "date": "2026-09-27"},
    }


def _write(path: Path, body: dict[str, Any]) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    target = path / f"{body['id']}.yaml"
    target.write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    return target


def _review(body: dict[str, Any]) -> dict[str, Any]:
    """Test-only metadata; never applied to dataset files."""
    case = Scenario.model_validate(body)
    return {
        **body,
        "reviewed_by": "agent:test/Qwen3.5-test",
        "reviewed_at": NOW.isoformat(),
        "review_method": "automated",
        "reviewed_content_sha256": content_hash(case),
    }


def test_checked_in_schema_matches_model() -> None:
    actual = json.loads(SCHEMA_PATH.read_text())
    expected = Scenario.model_json_schema()
    expected["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    assert actual == expected


def test_all_fifty_drafts_validate_and_have_exact_mix() -> None:
    dataset = load_dataset(DATASETS_DIR / "intake_en", include_unreviewed=True)
    assert len(dataset.scenarios) == 50
    assert Counter(case.category for case in dataset.scenarios) == {
        "chest_pain": 8,
        "stroke": 6,
        "sepsis": 8,
        "child": 6,
        "breathing": 4,
        "routine": 18,
    }
    assert len({case.id for case in dataset.scenarios}) == 50
    assert all(validate_scenario(case) == [] for case in dataset.scenarios)


def test_default_loader_rejects_unreviewed_and_skips_inbox(
    tmp_path: Path,
    raw_case: dict[str, Any],
) -> None:
    path = _write(tmp_path / "_inbox", raw_case)
    with pytest.raises(DatasetError, match="no reviewed scenarios"):
        load_dataset(tmp_path)
    with pytest.raises(DatasetError, match="inbox drafts"):
        load_dataset(path)
    assert len(load_dataset(tmp_path, include_unreviewed=True).scenarios) == 1
    moved = tmp_path / path.name
    path.rename(moved)
    with pytest.raises(DatasetError, match="missing reviewed"):
        load_dataset(tmp_path)


def test_review_binds_all_content_and_dataset_hash_is_review_time_independent(
    tmp_path: Path,
    raw_case: dict[str, Any],
) -> None:
    approved = _review(raw_case)
    path = _write(tmp_path, approved)
    before = load_dataset(tmp_path)
    approved["reviewed_at"] = "2026-09-28T09:05:00+00:00"
    _write(tmp_path, approved)
    assert load_dataset(tmp_path).sha256 == before.sha256
    for field in ("description", "generated_by", "patient", "turns", "expected", "device_vitals"):
        changed = deepcopy(approved)
        if field == "description":
            changed[field] += " Edited."
        elif field == "generated_by":
            changed[field]["tool"] = "different-tool"
        elif field == "patient":
            changed[field]["age_years"] = 31
        elif field == "turns":
            changed[field][1]["patient_text"] = "It is three out of ten."
        elif field == "expected":
            changed[field]["level_rationale"] += " Edited."
        else:
            changed[field] = {"temperature_c": 36.7}
        _write(tmp_path, changed)
        with pytest.raises(DatasetError, match="does not match current scenario content"):
            load_dataset(path)


def test_same_writer_cannot_claim_second_review(tmp_path: Path, raw_case: dict[str, Any]) -> None:
    reviewed = _review(raw_case)
    reviewed["reviewed_by"] = "agent:test/gpt6"
    _write(tmp_path, reviewed)
    with pytest.raises(DatasetError, match="different family"):
        load_dataset(tmp_path)


@pytest.mark.parametrize(
    "metadata",
    [
        {"review_method": "manual"},
        {"review_method": "made-up"},
        {"reviewed_by": "a-name-without-model"},
        {"reviewed_by": "agent:local/"},
    ],
)
def test_review_metadata_shape_is_enforced(
    tmp_path: Path,
    raw_case: dict[str, Any],
    metadata: dict[str, str],
) -> None:
    reviewed = {**_review(raw_case), **metadata}
    _write(tmp_path, reviewed)
    with pytest.raises(DatasetError, match="schema"):
        load_dataset(tmp_path)


def test_unknown_writer_model_cannot_be_promoted(tmp_path: Path, raw_case: dict[str, Any]) -> None:
    raw_case["generated_by"]["model"] = "unspecified"
    _write(tmp_path, _review(raw_case))
    with pytest.raises(DatasetError, match="model identity is not established"):
        load_dataset(tmp_path)


@pytest.mark.parametrize(
    "reviewer",
    [
        "agent:local/openai/gpt-6",
        "agent:local/gpt-5.6",
        "agent:local/unknown",
        "agent:local/qwen-default",
        "agent:gpt/mystery-model",
    ],
)
def test_review_admission_rejects_same_family_and_unknown_aliases(
    tmp_path: Path,
    raw_case: dict[str, Any],
    reviewer: str,
) -> None:
    reviewed = {**_review(raw_case), "reviewed_by": reviewer}
    _write(tmp_path, reviewed)
    with pytest.raises(DatasetError):
        load_dataset(tmp_path)


def test_changed_named_fixture_invalidates_review_and_dataset_hash(
    tmp_path: Path,
    raw_case: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    fixture = fixtures / "normal.yaml"
    fixture.write_text("bp_cuff: false\nseries:\n  - {hr: 78, spo2: 98, temp_c: 36.8}\n")
    monkeypatch.setattr(datasets_module, "DEVICE_SCENARIOS_DIR", fixtures)
    raw_case["device_vitals"] = "normal"
    scenario = Scenario.model_validate(raw_case)
    assert review_content(scenario)["resolved_device_vitals"] == {
        "heart_rate": 78,
        "spo2": 98,
        "temperature_c": 36.8,
    }
    path = _write(tmp_path / "dataset", _review(raw_case))
    before = load_dataset(path)
    fixture.write_text("bp_cuff: false\nseries:\n  - {hr: 79, spo2: 98, temp_c: 36.8}\n")
    assert review_content(scenario)["resolved_device_vitals"]["heart_rate"] == 79
    assert load_dataset(path, include_unreviewed=True).sha256 != before.sha256
    with pytest.raises(DatasetError, match="does not match current scenario content"):
        load_dataset(path)


def test_explicit_pain_and_location_can_come_from_complaint(raw_case: dict[str, Any]) -> None:
    raw_case["expected"]["facts"].update({"pain_present": "yes", "pain_location": "elbow"})
    assert validate_scenario(raw_case) == []
    case = Scenario.model_validate(raw_case)
    locations = [e for e in expected_entries(case) if e.key is FactKey.pain_location]
    assert len(locations) == 1
    assert isinstance(locations[0].base.source, TranscriptSpanSource)
    assert locations[0].base.source.quote == "My elbow hurts."


def test_zero_out_of_ten_does_not_support_positive_pain(raw_case: dict[str, Any]) -> None:
    raw_case["turns"][0]["patient_text"] = "I feel unwell."
    raw_case["turns"][1]["patient_text"] = "Zero out of ten."
    raw_case["expected"]["facts"].update(
        {
            "chief_complaint": "I feel unwell.",
            "pain_score": 0,
            "pain_present": "yes",
        }
    )
    assert any("not supported" in e for e in validate_scenario(raw_case))
    raw_case["expected"]["facts"]["pain_present"] = "no"
    assert validate_scenario(raw_case) == []


def test_duplicate_yaml_key_and_duplicate_ids_are_rejected(
    tmp_path: Path,
    raw_case: dict[str, Any],
) -> None:
    path = _write(tmp_path, raw_case)
    path.write_text(path.read_text() + "language: en\n")
    with pytest.raises(DatasetError, match="duplicate YAML key"):
        load_dataset(path, include_unreviewed=True)
    _write(tmp_path, raw_case)
    _write(tmp_path / "duplicate", raw_case)
    with pytest.raises(DatasetError, match="duplicate scenario id"):
        load_dataset(tmp_path, include_unreviewed=True)


def test_gold_never_leaks_to_extractor_context_or_questions(raw_case: dict[str, Any]) -> None:
    case = Scenario.model_validate(raw_case)
    edited = deepcopy(raw_case)
    edited["expected"]["facts"] = {"chief_complaint": "SECRET_GOLD_VALUE"}
    edited_case = Scenario.model_validate(edited)
    assert transcript(case) == transcript(edited_case)
    assert context_entries(case) == context_entries(edited_case)
    assert {entry.key for entry in context_entries(case)} == {
        FactKey.patient_age_years,
        FactKey.patient_sex,
    }


def test_gold_provenance_references_actual_answer_with_exact_offsets(
    raw_case: dict[str, Any],
) -> None:
    case = Scenario.model_validate(raw_case)
    turns, keys = transcript(case)
    by_id = {turn.turn_id: turn for turn in turns}
    assert len(turns) == 6
    assert len(keys) == 3
    for entry in expected_entries(case):
        assert entry.provenance is Provenance.PATIENT_STATED
        source = entry.base.source
        assert isinstance(source, TranscriptSpanSource)
        turn = by_id[source.turn_id]
        assert turn.role is TurnRole.patient
        assert turn.text[source.start : source.end] == source.quote
    assert expected_entries(case) == expected_entries(case)


@pytest.mark.parametrize(
    "mutate,fragment",
    [
        (lambda b: b["patient"].update(age_years=True), "schema"),
        (lambda b: b["expected"]["facts"].update(pain_score="3"), "not supported"),
        (lambda b: b["expected"]["facts"].update(pain_score=8), "not supported"),
        (lambda b: b["expected"]["facts"].update(pain_score=[3]), "must be a scalar"),
        (lambda b: b["expected"]["facts"].update({"vitals.spo2": 98}), "outside extractor scope"),
        (
            lambda b: b["expected"]["facts"].update({"patient.age_years": 30}),
            "known patient context",
        ),
        (lambda b: b["expected"].update(alerts=["FAST_STROKE"]), "rule alert mismatch"),
        (lambda b: b["device_vitals"].update(heart_rate=True), "requires a number"),
        (lambda b: b.update(device_vitals="../../unknown"), "unknown device simulator"),
        (lambda b: b.update(description="Reach me at synthetic@example.org"), "PHI denylist"),
        (lambda b: b.update(description="Call 07123456789"), "PHI denylist"),
        (lambda b: b.update(description="My postcode is SW1A 1AA"), "PHI denylist"),
    ],
)
def test_validation_rejects_invalid_or_unsupported_content(
    raw_case: dict[str, Any],
    mutate: Any,
    fragment: str,
) -> None:
    mutate(raw_case)
    assert any(fragment in error for error in validate_scenario(raw_case))


def test_named_device_and_nurse_override_keep_correct_provenance(raw_case: dict[str, Any]) -> None:
    raw_case.update(device_vitals="normal", nurse_vitals={"heart_rate": 105})
    case = Scenario.model_validate(raw_case)
    entries = context_entries(case)
    heart_rates = [entry for entry in entries if entry.key is FactKey.vitals_heart_rate]
    assert [entry.base.provenance for entry in heart_rates] == [
        Provenance.DEVICE_MEASURED,
        Provenance.NURSE_CONFIRMED,
    ]
    current = next(e for e in current_entries(entries) if e.key is FactKey.vitals_heart_rate)
    assert current.provenance is Provenance.NURSE_CONFIRMED
    assert current.fact.model_dump()["value"] == 105
