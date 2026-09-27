"""Hand-counted scoring examples and invariants, independent of models, providers or services."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from itertools import permutations
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from careflow_eval.metrics import (
    NORMALIZATION_VERSION,
    MetricResult,
    alert_recall,
    dropped_fact_rate,
    extraction_incomplete_rate,
    false_alert_rate,
    field_f1,
    field_precision,
    field_recall,
    get_metric,
    latency_p50_ms,
    latency_p95_ms,
    metric_names,
    register_metric,
)


def facts(*pairs: tuple[str, Any]) -> dict[str, Any]:
    return {"facts": [{"key": key, "value": value} for key, value in pairs]}


def test_hand_counted_wrong_values_add_both_false_positive_and_false_negative() -> None:
    actual = [
        facts(
            ("chief_complaint", "Difficulty breathing"),
            ("pain_score", 6),
            ("symptoms", "vomiting"),
            ("vitals.temperature", 38),
        )
    ]
    gold = [
        facts(("chief_complaint", "shortness of breath"), ("pain_score", 4), ("symptoms", "nausea"))
    ]
    result = field_f1(actual, gold)
    assert result.value == pytest.approx(2 / 7)
    assert {key: result.details[key] for key in ("tp", "fp", "fn")} == {"tp": 1, "fp": 3, "fn": 2}
    assert field_precision(actual, gold).value == 1 / 4
    assert field_recall(actual, gold).value == 1 / 3
    assert result.details["per_key"]["pain_score"] == {
        "tp": 0,
        "fp": 1,
        "fn": 1,
        "precision": 0,
        "recall": 0,
        "f1": 0,
    }
    assert result.details["per_key"]["vitals.temperature"]["recall"] is None
    assert result.details["normalization_version"] == NORMALIZATION_VERSION


@pytest.mark.parametrize(
    ("actual", "gold", "matches"),
    [
        (38.6, 38.5, True),
        (38.5, 38.6, True),
        (-4.2, -4.1, True),
        (38.60001, 38.5, False),
        (-1e-40, 0.1, False),
        (1e-40, 0.1, True),
        (4, 4.0, True),
        (True, True, True),
        (False, True, False),
        (True, 1, False),
        (False, 0, False),
        (1, True, False),
        ("4", 4, False),
        ("true", True, False),
        (None, None, True),
        (None, "unknown", False),
    ],
)
def test_scalar_value_boundaries(actual: Any, gold: Any, matches: bool) -> None:
    assert field_f1([facts(("key", actual))], [facts(("key", gold))]).value == int(matches)


def test_text_normalization_is_explicit_and_complaint_synonyms_have_limited_scope() -> None:
    assert (
        field_f1(
            [facts((" CHIEF_COMPLAINT ", "  DIFFICULTY   breathing "))],
            [facts(("chief_complaint", "Shortness of breath"))],
        ).value
        == 1
    )
    assert field_f1([facts(("symptoms", "Straße"))], [facts(("symptoms", "STRASSE"))]).value == 1
    assert (
        field_f1(
            [facts(("pain.location", "tummy pain"))], [facts(("pain.location", "abdominal pain"))]
        ).value
        == 0
    )
    assert (
        field_f1(
            [facts(("chief_complaint", "no shortness of breath"))],
            [facts(("chief_complaint", "short of breath"))],
        ).value
        == 0
    )
    assert (
        field_f1(
            [facts(("chief_complaint", "chest discomfort"))],
            [facts(("chief_complaint", "chest pain"))],
        ).value
        == 0
    )


def test_maximum_matching_reassigns_an_ambiguous_numeric_match_in_every_order() -> None:
    # 0.1 fits either gold value, but 0.0 only fits 0.0. Greedy consumes 0.0 and loses a match.
    for actual in permutations([0.1, 0.0]):
        for gold in permutations([0.0, 0.2]):
            score = field_f1(
                [facts(*(("repeated", value) for value in actual))],
                [facts(*(("repeated", value) for value in gold))],
            )
            assert score.value == 1
            assert score.details["tp"] == 2


def test_duplicate_symptoms_remain_distinct_slots_not_a_dict_or_set() -> None:
    actual = [facts(("symptoms", "Nausea"), ("symptoms", "nausea"), ("symptoms", "fatigue"))]
    gold = [facts(("symptoms", "nausea"), ("symptoms", "fatigue"))]
    score = field_f1(actual, gold)
    assert score.value == 4 / 5
    assert (score.details["tp"], score.details["fp"], score.details["fn"]) == (2, 1, 0)


def test_structured_list_values_use_recursive_one_to_one_matching() -> None:
    actual = [facts(("structured", {"items": [0.1, 0.0], "label": " EXAMPLE "}))]
    gold = [facts(("structured", {"label": "example", "items": [0.0, 0.2]}))]
    assert field_f1(actual, gold).value == 1
    assert (
        field_f1([facts(("structured", ["x", "x"]))], [facts(("structured", ["x", "y"]))]).value
        == 0
    )
    assert field_f1([facts(("structured", ["x"]))], [facts(("structured", "x"))]).value == 0


def test_facts_never_match_across_cases_and_failures_stay_in_the_denominator() -> None:
    score = field_f1([facts(), facts(("pain_score", 5))], [facts(("pain_score", 5)), facts()])
    assert (score.value, score.details["tp"], score.details["fp"], score.details["fn"]) == (
        0,
        0,
        1,
        1,
    )
    assert (
        field_recall(
            [facts(("pain_score", 5)), facts()],
            [facts(("pain_score", 5)), facts(("pain_score", 5))],
        ).value
        == 0.5
    )


def test_undefined_field_denominators_are_null_in_strict_json() -> None:
    empty = field_f1([facts()], [facts()])
    assert empty.value is None
    assert empty.details["precision"] is None and empty.details["recall"] is None
    assert '"value": null' in json.dumps(asdict(empty), allow_nan=False)
    missing = field_f1([facts()], [facts(("pain_score", 5))])
    assert missing.value == 0
    assert missing.details["precision"] is None and missing.details["recall"] == 0
    assert field_f1([], []).value is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("nested", [False, True])
def test_nonfinite_gold_and_predictions_are_rejected(bad: float, nested: bool) -> None:
    value = {"nested": [bad]} if nested else bad
    for actual, gold in (
        ([facts(("key", value))], [facts()]),
        ([facts()], [facts(("key", value))]),
    ):
        with pytest.raises(ValueError, match="finite"):
            field_f1(actual, gold)


@given(
    actual=st.lists(st.integers(-5, 5), max_size=20),
    gold=st.lists(st.integers(-5, 5), max_size=20),
)
def test_integer_multisets_obey_counting_and_permutation_invariants(
    actual: list[int], gold: list[int]
) -> None:
    # Distinct integers are more than the tolerance apart: the multiset intersection is an oracle.
    expected_matches = sum((Counter(actual) & Counter(gold)).values())
    result = field_f1(
        [facts(*(("key", value) for value in actual))],
        [facts(*(("key", value) for value in gold))],
    )
    reversed_result = field_f1(
        [facts(*(("key", value) for value in reversed(actual)))],
        [facts(*(("key", value) for value in reversed(gold)))],
    )
    assert result == reversed_result
    assert result.details["tp"] == expected_matches
    assert result.details["tp"] + result.details["fp"] == len(actual)
    assert result.details["tp"] + result.details["fn"] == len(gold)
    assert result.value is None or 0 <= result.value <= 1


def test_hand_counted_alerts_separate_rule_recall_from_benign_false_alerts() -> None:
    actual = [
        {"alerts": ["sepsis", "chest", "chest"]},
        {"alerts": ["fast", "sepsis"]},
        {"alerts": []},
        {"alerts": []},
    ]
    gold = [{"alerts": ["sepsis", "fast"]}, {"alerts": []}, {"alerts": []}, {"alerts": ["chest"]}]
    score = alert_recall(actual, gold)
    assert score.value == 1 / 3
    assert score.details["unexpected_alerts"] == 3  # includes chest on the positive first case
    assert score.details["expected_alerts"] == 3 and score.details["missed_alerts"] == 2
    assert false_alert_rate(actual, gold).value == 1 / 2
    assert score.details["per_rule"]["sepsis"] == {
        "expected": 1,
        "detected": 1,
        "missed": 0,
        "recall": 1,
        "unexpected": 1,
        "benign_false_alert_cases": 1,
        "benign_false_alert_rate": 0.5,
    }
    assert score.details["per_rule"]["chest"]["unexpected"] == 1
    assert score.details["per_rule"]["chest"]["benign_false_alert_cases"] == 0


def test_alert_denominators_and_unexpected_only_rules() -> None:
    assert alert_recall([{"alerts": []}], [{"alerts": []}]).value is None
    assert false_alert_rate([{"alerts": []}], [{"alerts": ["fast"]}]).value is None
    assert false_alert_rate([{"alerts": []}], [{"alerts": []}]).value == 0
    extra = alert_recall([{"alerts": ["unknown"]}], [{"alerts": []}])
    assert extra.details["per_rule"]["unknown"]["recall"] is None
    assert false_alert_rate([], []).value is None


def test_operational_rates_count_cases_and_report_dropped_candidates_separately() -> None:
    actual = [
        {"extraction_incomplete": True, "dropped_count": 3},
        {"extraction_incomplete": False, "dropped_count": 1},
        {"extraction_incomplete": False, "dropped_count": 0},
    ]
    gold = [{}, {}, {}]
    assert extraction_incomplete_rate(actual, gold).value == 1 / 3
    dropped = dropped_fact_rate(actual, gold)
    assert dropped.value == 2 / 3  # not 4/3, nor an invented fraction of all candidate facts
    assert dropped.details["dropped_total"] == 4
    assert dropped.details["candidate_rejection_rate"] is None
    assert extraction_incomplete_rate([], []).value is None
    assert dropped_fact_rate([], []).value is None


def test_nearest_rank_percentiles_are_not_interpolated_and_missing_samples_are_counted() -> None:
    actual = [{"latency_ms": value} for value in reversed(range(1, 21))] + [
        {},
        {"latency_ms": None},
    ]
    gold = [{} for _ in actual]
    p50, p95 = latency_p50_ms(actual, gold), latency_p95_ms(actual, gold)
    assert p50.value == 10  # even sample count: nearest rank, not median 10.5
    assert p95.value == 19
    assert p95.details == {
        "case_count": 22,
        "sample_count": 20,
        "missing_count": 2,
        "percentile": 95,
        "method": "nearest_rank",
        "units": "ms",
    }
    assert latency_p95_ms([{}], [{}]).value is None
    assert latency_p50_ms([{"latency_ms": 0}], [{}]).value == 0


@pytest.mark.parametrize("bad", [True, "2", -1, float("nan"), float("inf")])
def test_invalid_latency_samples_cannot_make_a_report(bad: Any) -> None:
    with pytest.raises((TypeError, ValueError)):
        latency_p95_ms([{"latency_ms": bad}], [{}])


def test_omitted_operational_flags_are_errors_not_silent_success() -> None:
    with pytest.raises(TypeError, match="explicitly"):
        extraction_incomplete_rate([{}], [{}])
    with pytest.raises(ValueError, match="integer"):
        dropped_fact_rate([{"dropped_count": True}], [{}])


def test_registry_and_metrics_reject_unknown_names_and_missing_cases() -> None:
    assert get_metric("extraction_field_f1") is get_metric("field_f1")
    assert {"field_f1", "alert_recall", "dropped_fact_rate", "latency_p95_ms"} <= set(
        metric_names()
    )
    with pytest.raises(KeyError):
        get_metric("not_a_metric")
    with pytest.raises(ValueError, match="already registered"):
        register_metric("field_f1")(field_f1)
    with pytest.raises(ValueError, match="Invalid metric name"):
        register_metric("unsafe name")
    for name in metric_names():
        with pytest.raises(ValueError, match="same number"):
            get_metric(name)([{}], [])
    assert isinstance(field_f1([], []), MetricResult)
