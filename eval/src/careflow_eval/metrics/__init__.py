"""Pure evaluation metrics, registered when this package is imported."""

from careflow_eval.metrics.alerts import alert_counts, alert_recall, false_alert_rate
from careflow_eval.metrics.extraction import (
    COMPLAINT_SYNONYMS,
    NORMALIZATION_VERSION,
    NUMERIC_TOLERANCE,
    field_counts,
    field_f1,
    field_precision,
    field_recall,
)
from careflow_eval.metrics.operational import (
    dropped_fact_rate,
    extraction_incomplete_rate,
    latency_p50_ms,
    latency_p95_ms,
)
from careflow_eval.metrics.registry import (
    METRICS,
    Cases,
    Metric,
    MetricResult,
    get_metric,
    metric_names,
    register_metric,
)

__all__ = [
    "COMPLAINT_SYNONYMS",
    "METRICS",
    "NORMALIZATION_VERSION",
    "NUMERIC_TOLERANCE",
    "Cases",
    "Metric",
    "MetricResult",
    "alert_counts",
    "alert_recall",
    "dropped_fact_rate",
    "extraction_incomplete_rate",
    "false_alert_rate",
    "field_counts",
    "field_f1",
    "field_precision",
    "field_recall",
    "get_metric",
    "latency_p50_ms",
    "latency_p95_ms",
    "metric_names",
    "register_metric",
]
