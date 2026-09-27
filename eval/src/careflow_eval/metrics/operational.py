"""Case-based quality rates and nearest-rank latency percentiles, in milliseconds.

Incomplete rate counts prediction cases whose ``extraction_incomplete`` is true. Dropped-fact
rate counts cases whose ``dropped_count`` is positive; details also publish the total rejected
candidates. We cannot compute a candidate rejection fraction without the candidate denominator.
Missing or null ``latency_ms`` is excluded only from latency samples and counted in details;
it never removes a case from a quality denominator. Historical/replay timing labels belong to
the runner. These metrics do not imply end-to-end nurse-alert or live model latency.
"""

from __future__ import annotations

import math

from careflow_eval.metrics.registry import (
    Cases,
    MetricResult,
    aligned_cases,
    ratio,
    register_metric,
)


@register_metric("extraction_incomplete_rate")
def extraction_incomplete_rate(predictions: Cases, expected: Cases) -> MetricResult:
    cases = aligned_cases(predictions, expected)
    incomplete = 0
    for prediction, _ in cases:
        value = prediction.get("extraction_incomplete")
        if type(value) is not bool:
            raise TypeError("extraction_incomplete must be explicitly true or false for every case")
        incomplete += value
    return MetricResult(
        ratio(incomplete, len(cases)),
        {"case_count": len(cases), "incomplete_cases": incomplete, "denominator": "all_cases"},
    )


@register_metric("dropped_fact_rate")
def dropped_fact_rate(predictions: Cases, expected: Cases) -> MetricResult:
    cases = aligned_cases(predictions, expected)
    affected = dropped = 0
    for prediction, _ in cases:
        value = prediction.get("dropped_count")
        if type(value) is not int or value < 0:
            raise ValueError("dropped_count must be a nonnegative integer for every case")
        affected += value > 0
        dropped += value
    return MetricResult(
        ratio(affected, len(cases)),
        {
            "case_count": len(cases),
            "cases_with_dropped_facts": affected,
            "dropped_total": dropped,
            "denominator": "all_cases",
            "definition": "fraction_of_cases_with_at_least_one_rejected_candidate",
            "candidate_rejection_rate": None,
        },
    )


def _latency(predictions: Cases, expected: Cases, percentile: int) -> MetricResult:
    cases = aligned_cases(predictions, expected)
    samples: list[float] = []
    for prediction, _ in cases:
        value = prediction.get("latency_ms")
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise TypeError("latency_ms must be a nonnegative finite number or null")
        if not math.isfinite(value) or value < 0:
            raise ValueError("latency_ms must be a nonnegative finite number or null")
        samples.append(float(value))
    samples.sort()
    rank = (len(samples) * percentile + 99) // 100
    return MetricResult(
        samples[rank - 1] if samples else None,
        {
            "case_count": len(cases),
            "sample_count": len(samples),
            "missing_count": len(cases) - len(samples),
            "percentile": percentile,
            "method": "nearest_rank",
            "units": "ms",
        },
    )


@register_metric("latency_p50_ms")
def latency_p50_ms(predictions: Cases, expected: Cases) -> MetricResult:
    return _latency(predictions, expected, 50)


@register_metric("latency_p95_ms")
def latency_p95_ms(predictions: Cases, expected: Cases) -> MetricResult:
    return _latency(predictions, expected, 95)


__all__ = ["dropped_fact_rate", "extraction_incomplete_rate", "latency_p50_ms", "latency_p95_ms"]
