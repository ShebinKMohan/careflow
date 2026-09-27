"""Alert recall by rule and false-alert rate among benign cases (no expected alerts).

An alert is a (case, rule-id) pair, so duplicate emissions of one rule in a case count once.
Unexpected alerts on positive cases are reported as well as alerts on benign cases; they are not
silently folded into the benign-case denominator. A failure's empty alert list remains a case.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from careflow_eval.metrics.registry import (
    Cases,
    MetricResult,
    aligned_cases,
    ratio,
    register_metric,
)


def _rules(case: Mapping[str, Any]) -> set[str]:
    alerts = case.get("alerts")
    if not isinstance(alerts, list) or any(
        not isinstance(rule, str) or not rule.strip() for rule in alerts
    ):
        raise TypeError("Each alert metric case requires an alerts list of nonempty rule ids")
    return set(alerts)


def alert_counts(predictions: Cases, expected: Cases) -> dict[str, Any]:
    # Per-rule counters: detected, missed, unexpected, unexpected in a benign case.
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    benign_cases = false_alert_cases = 0
    for predicted_case, expected_case in aligned_cases(predictions, expected):
        actual, wanted = _rules(predicted_case), _rules(expected_case)
        if not wanted:
            benign_cases += 1
            false_alert_cases += bool(actual)
        for rule in actual | wanted:
            row = counts[rule]
            row[0] += rule in actual and rule in wanted
            row[1] += rule in wanted and rule not in actual
            row[2] += rule in actual and rule not in wanted
            row[3] += rule in actual and not wanted
    detected, missed, unexpected = (
        sum(row[index] for row in counts.values()) for index in range(3)
    )
    return {
        "case_count": len(predictions),
        "expected_alerts": detected + missed,
        "detected_alerts": detected,
        "missed_alerts": missed,
        "unexpected_alerts": unexpected,
        "recall": ratio(detected, detected + missed),
        "benign_cases": benign_cases,
        "false_alert_cases": false_alert_cases,
        "false_alert_rate": ratio(false_alert_cases, benign_cases),
        "per_rule": {
            rule: {
                "expected": found + absent,
                "detected": found,
                "missed": absent,
                "recall": ratio(found, found + absent),
                "unexpected": extra,
                "benign_false_alert_cases": benign_extra,
                "benign_false_alert_rate": ratio(benign_extra, benign_cases),
            }
            for rule, (found, absent, extra, benign_extra) in sorted(counts.items())
        },
    }


@register_metric("alert_recall")
def alert_recall(predictions: Cases, expected: Cases) -> MetricResult:
    details = alert_counts(predictions, expected)
    return MetricResult(details["recall"], details)


@register_metric("false_alert_rate")
def false_alert_rate(predictions: Cases, expected: Cases) -> MetricResult:
    details = alert_counts(predictions, expected)
    return MetricResult(details["false_alert_rate"], details)


__all__ = ["alert_counts", "alert_recall", "false_alert_rate"]
