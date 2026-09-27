"""Pure metric registry. Inputs are aligned case dictionaries, never provider objects.

Each metric accepts ``(predictions, expected)`` with one dictionary per evaluated case, in the
same order. A failed case stays in both sequences. Field metrics read ``facts`` as a list of
``{"key": str, "value": JSON value}`` slots; alert metrics read ``alerts`` as rule-id lists.
Operational metrics read their documented prediction fields. Unknown dictionary keys are ignored.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

type Cases = Sequence[Mapping[str, Any]]


@dataclass(frozen=True)
class MetricResult:
    """A score and its auditable counts. Undefined scores use None, serialized as JSON null."""

    value: float | None
    details: dict[str, Any]


type Metric = Callable[[Cases, Cases], MetricResult]

_metrics: dict[str, Metric] = {}
METRICS: Mapping[str, Metric] = MappingProxyType(_metrics)


def register_metric(name: str) -> Callable[[Metric], Metric]:
    """Register a pure function; a duplicate name is an error rather than a silent replacement."""
    if re.fullmatch(r"[a-z][a-z0-9_]*", name) is None:
        raise ValueError(f"Invalid metric name: {name!r}")

    def decorate(metric: Metric) -> Metric:
        if name in _metrics:
            raise ValueError(f"Metric already registered: {name}")
        _metrics[name] = metric
        return metric

    return decorate


def get_metric(name: str) -> Metric:
    """Look up a registered metric, raising KeyError for an unknown name."""
    return _metrics[name]


def metric_names() -> tuple[str, ...]:
    return tuple(sorted(_metrics))


def aligned_cases(
    predictions: Cases, expected: Cases
) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    """Reject missing cases instead of silently truncating metric denominators with zip."""
    if len(predictions) != len(expected):
        raise ValueError("Predictions and expected must contain the same number of cases")
    if any(not isinstance(case, Mapping) for case in (*predictions, *expected)):
        raise TypeError("Every prediction and expected case must be a mapping")
    return list(zip(predictions, expected, strict=True))


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


__all__ = [
    "METRICS",
    "Cases",
    "Metric",
    "MetricResult",
    "get_metric",
    "metric_names",
    "register_metric",
]
