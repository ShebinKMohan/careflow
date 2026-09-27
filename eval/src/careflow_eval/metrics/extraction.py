"""Field metrics over per-case multisets of key/value facts, ignoring provenance metadata.

Normalization v1 is fixed before collecting a baseline: keys and string values are trimmed,
whitespace-collapsed and case-folded. Only ``chief_complaint`` uses the small synonym map below;
there is no stemming, substring matching or learned/fuzzy equivalence. Numeric values match at
absolute distance <= 0.1, using exact Decimal(str(value)) fractions; booleans never match numbers
or strings. Fraction arithmetic prevents the ambient Decimal context rounding a near-boundary
difference back to 0.1.
Non-finite numbers are invalid. JSON objects require the same keys, recursively equal values;
JSON lists compare as multisets using maximum one-to-one matching (lengths must also match).

Each fact slot is counted once. The caller expands a domain list such as expected symptoms into
separate slots, matching one extracted FactEntry per symptom. Duplicate slots are not collapsed.
Matching occurs within a case and key, never across patients. A wrong value contributes FP + FN.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from fractions import Fraction
from types import MappingProxyType
from typing import Any, Final

from careflow_eval.metrics.registry import (
    Cases,
    MetricResult,
    aligned_cases,
    ratio,
    register_metric,
)

NORMALIZATION_VERSION: Final = "field-values-v1"
NUMERIC_TOLERANCE: Final = Decimal("0.1")
COMPLAINT_SYNONYMS: Mapping[str, str] = MappingProxyType(
    {
        "shortness of breath": "short of breath",
        "breathlessness": "short of breath",
        "difficulty breathing": "short of breath",
        "stomach pain": "abdominal pain",
        "tummy pain": "abdominal pain",
    }
)


def _text(value: str) -> str:
    return " ".join(value.split()).casefold()


def _validate_value(value: Any) -> None:
    if value is None or isinstance(value, str | bool):
        return
    if isinstance(value, int | float):
        if not Decimal(str(value)).is_finite():
            raise ValueError("Fact numbers must be finite")
        return
    if isinstance(value, list):
        for item in value:
            _validate_value(item)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _validate_value(item)
        return
    raise TypeError("Fact values must be JSON scalars, lists or string-keyed objects")


def _maximum_matches(
    left: Sequence[Any], right: Sequence[Any], compatible: Callable[[Any, Any], bool]
) -> int:
    """Maximum bipartite matching with augmenting paths, not order-dependent greedy pairing."""
    edges = [[j for j, value in enumerate(right) if compatible(item, value)] for item in left]
    owners: dict[int, int] = {}

    def augment(index: int, seen: set[int]) -> bool:
        for candidate in edges[index]:
            if candidate in seen:
                continue
            seen.add(candidate)
            if candidate not in owners or augment(owners[candidate], seen):
                owners[candidate] = index
                return True
        return False

    return sum(augment(index, set()) for index in range(len(left)))


def _equal(left: Any, right: Any, *, complaint: bool) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is bool and type(right) is bool and left == right
    if isinstance(left, int | float) and isinstance(right, int | float):
        difference = abs(Fraction(Decimal(str(left))) - Fraction(Decimal(str(right))))
        return difference <= Fraction(NUMERIC_TOLERANCE)
    if isinstance(left, str) and isinstance(right, str):
        a, b = _text(left), _text(right)
        if complaint:
            a, b = COMPLAINT_SYNONYMS.get(a, a), COMPLAINT_SYNONYMS.get(b, b)
        return a == b
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and _maximum_matches(
            left, right, lambda a, b: _equal(a, b, complaint=complaint)
        ) == len(left)
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _equal(left[key], right[key], complaint=complaint) for key in left
        )
    return left is None and right is None


def _facts(case: Mapping[str, Any]) -> dict[str, list[Any]]:
    facts = case.get("facts")
    if not isinstance(facts, list):
        raise TypeError(
            "Each field metric case requires a facts list (empty on extraction failure)"
        )
    by_key: dict[str, list[Any]] = defaultdict(list)
    for fact in facts:
        if (
            not isinstance(fact, Mapping)
            or not isinstance(fact.get("key"), str)
            or "value" not in fact
        ):
            raise TypeError("Each fact requires a string key and a value")
        key = _text(fact["key"])
        if not key:
            raise ValueError("Fact keys must not be empty")
        _validate_value(fact["value"])
        by_key[key].append(fact["value"])
    return by_key


def _scores(tp: int, fp: int, fn: int) -> dict[str, int | float | None]:
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": ratio(tp, tp + fp),
        "recall": ratio(tp, tp + fn),
        "f1": ratio(2 * tp, 2 * tp + fp + fn),
    }


def field_counts(predictions: Cases, expected: Cases) -> dict[str, Any]:
    """Return micro and per-key TP/FP/FN, precision, recall and F1 over aligned cases."""
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for predicted_case, expected_case in aligned_cases(predictions, expected):
        predicted, gold = _facts(predicted_case), _facts(expected_case)
        for key in predicted.keys() | gold.keys():
            actual, wanted = predicted.get(key, []), gold.get(key, [])
            matched = _maximum_matches(
                actual,
                wanted,
                lambda a, b, key=key: _equal(a, b, complaint=key == "chief_complaint"),
            )
            totals = counts[key]
            totals[0] += matched
            totals[1] += len(actual) - matched
            totals[2] += len(wanted) - matched
    tp, fp, fn = (sum(row[index] for row in counts.values()) for index in range(3))
    return {
        **_scores(tp, fp, fn),
        "case_count": len(predictions),
        "predicted_facts": tp + fp,
        "expected_facts": tp + fn,
        "normalization_version": NORMALIZATION_VERSION,
        "per_key": {key: _scores(*counts[key]) for key in sorted(counts)},
    }


@register_metric("field_precision")
def field_precision(predictions: Cases, expected: Cases) -> MetricResult:
    details = field_counts(predictions, expected)
    return MetricResult(details["precision"], details)


@register_metric("field_recall")
def field_recall(predictions: Cases, expected: Cases) -> MetricResult:
    details = field_counts(predictions, expected)
    return MetricResult(details["recall"], details)


@register_metric("extraction_field_f1")
@register_metric("field_f1")
def field_f1(predictions: Cases, expected: Cases) -> MetricResult:
    details = field_counts(predictions, expected)
    return MetricResult(details["f1"], details)


__all__ = [
    "COMPLAINT_SYNONYMS",
    "NORMALIZATION_VERSION",
    "NUMERIC_TOLERANCE",
    "field_counts",
    "field_f1",
    "field_precision",
    "field_recall",
]
