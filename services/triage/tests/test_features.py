"""One feature path: row -> StructuredFacts -> features, and the leakage guard."""

from __future__ import annotations

import math

import numpy as np
import pytest

from careflow_triage.ktas.features import (
    FEATURE_NAMES,
    complaint_category,
    facts_to_features,
    feature_fact_ids,
    mapping,
    row_to_facts,
)
from careflow_triage.ktas.load import COLUMNS

LEAKAGE = {
    "KTAS_RN", "KTAS_expert", "Diagnosis in ED", "Disposition",
    "Error_group", "Length of stay_min", "KTAS duration_min", "mistriage",
}  # fmt: skip


def row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {c: math.nan for c in COLUMNS}
    base.update({
        "Age": 67.4, "Sex": 2.0, "Arrival mode": 2.0, "Injury": 1.0,
        "Chief_complain": "ant. chest pain", "Mental": 3.0, "Pain": 1.0, "NRS_pain": 7.0,
        "SBP": 88.0, "DBP": 50.0, "HR": 128.0, "RR": 26.0, "BT": 39.4, "Saturation": 91.0,
        "KTAS_RN": 2.0, "KTAS_expert": 1.0, "Disposition": 3.0,
    })  # fmt: skip
    base.update(overrides)
    return base


def test_the_feature_list_is_pinned_and_free_of_leakage() -> None:
    assert FEATURE_NAMES == (
        "age_years", "sex_male", "arrival_mode", "injury", "acvpu", "pain_present", "pain_score",
        "systolic_bp", "diastolic_bp", "heart_rate", "respiratory_rate", "temperature_c", "spo2",
        "complaint_category",
    )  # fmt: skip
    used = set(mapping()["columns"])
    assert used.isdisjoint(LEAKAGE) and set(mapping()["leakage"]) == LEAKAGE
    assert used.isdisjoint(mapping()["context"])


def test_leakage_columns_cannot_change_the_features() -> None:
    changed: dict[str, object] = {
        "KTAS_RN": 5.0, "KTAS_expert": 5.0, "Diagnosis in ED": "Cardiac arrest",
        "Disposition": 7.0, "Error_group": 9.0, "Length of stay_min": 9999.0,
        "KTAS duration_min": "99,9", "mistriage": 2.0,
    }  # fmt: skip
    assert set(changed) == LEAKAGE
    base = row_to_facts(row())
    assert np.array_equal(
        facts_to_features(base), facts_to_features(row_to_facts(row(**changed))), equal_nan=True
    )
    assert row_to_facts(row(**changed)) == base


def test_row_to_facts_follows_the_verified_codes() -> None:
    facts = row_to_facts(row(), index=7)
    assert facts.patient and facts.patient.age_years and facts.patient.age_years.value == 67
    assert facts.patient.sex and facts.patient.sex.value.code == "male"
    assert facts.arrival_mode and facts.arrival_mode.value.code.value == "public_ambulance"
    assert facts.injury and facts.injury.value.value == "no"
    assert facts.vitals and facts.vitals.acvpu and facts.vitals.acvpu.value.code.value == "P"
    assert facts.pain_present and facts.pain_present.value.value == "yes"
    assert facts.complaint_category and facts.complaint_category.value.code == "chest_pain"
    assert facts.complaint_category.provenance.value == "MODEL_INFERRED"
    assert facts.vitals.heart_rate and facts.vitals.heart_rate.provenance.value == "NURSE_CONFIRMED"


def test_features_follow_the_list_and_missing_is_nan() -> None:
    x = facts_to_features(row_to_facts(row(Saturation=math.nan, NRS_pain=math.nan)))
    values = dict(zip(FEATURE_NAMES, x, strict=True))
    assert values["age_years"] == 67 and values["sex_male"] == 1 and values["heart_rate"] == 128
    assert values["acvpu"] == 3.0  # A, C, V, P, U -> P is index 3
    assert math.isnan(values["spo2"]) and math.isnan(values["pain_score"])
    ids = feature_fact_ids(row_to_facts(row(Saturation=math.nan)))
    assert ids["spo2"] == [] and len(ids["heart_rate"]) == 1


def test_unknown_codes_are_not_recorded() -> None:
    facts = row_to_facts(row(Sex=9.0, Mental=math.nan))
    assert facts.patient and facts.patient.sex is None
    assert facts.vitals and facts.vitals.acvpu is None


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("ant. chest pain", "chest_pain"), ("chest palpitation", "palpitation"),
        ("abd pain", "abdominal_pain"), ("open wound", "injury"),
        ("rt. side motor weakness", "neurological"), ("general weakness", "general_weakness"),
        ("dz - dizziness", "dizziness"), ("h-headache", "headache"), ("ha", "headache"),
        ("hematemesis", "gastrointestinal"), ("ocular pain, lt.", "eye_ent"),
        ("ear pain", "eye_ent"), ("heartburn", "other"), ("fear", "other"),
        ("both leg pain", "musculoskeletal"),
        ("dyspnea", "breathing_difficulty"), ("", "other"),
    ],
)  # fmt: skip
def test_complaint_keywords(text: str, code: str) -> None:
    assert complaint_category(text)[0] == code


def test_implausible_values_are_not_recorded() -> None:
    facts = row_to_facts(row(Saturation=101.0, NRS_pain=12.0, HR=-3.0))
    assert facts.vitals and facts.vitals.spo2 is None and facts.vitals.heart_rate is None
    assert facts.pain_score is None
