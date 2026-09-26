"""Triage metrics. Levels are 1 (most urgent) to 5; "less urgent" means a higher number.

Under-triage (levels 1-2): the share of true level 1-2 cases predicted less urgent, with a Wilson
95% interval. Over-triage: the share of all cases predicted more urgent. Also macro-F1, top-label
ECE (15 bins) and the confusion matrix.
"""

from __future__ import annotations

import math
from typing import Any, Final

import numpy as np
from sklearn.metrics import confusion_matrix, f1_score

LEVELS: Final = (1, 2, 3, 4, 5)


def wilson(successes: int, total: int, z: float = 1.959964) -> tuple[float, float]:
    if total == 0:
        return (math.nan, math.nan)
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return (max(0.0, centre - half), min(1.0, centre + half))


def under_triage_urgent(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, Any]:
    urgent = y_true <= 2
    total = int(urgent.sum())
    missed = int((y_pred[urgent] > y_true[urgent]).sum())
    low, high = wilson(missed, total)
    return {
        "count": missed,
        "of": total,
        "rate": missed / total if total else math.nan,
        "ci95": [low, high],
    }


def over_triage(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, Any]:
    over = int((y_pred < y_true).sum())
    low, high = wilson(over, len(y_true))
    return {"count": over, "of": len(y_true), "rate": over / len(y_true), "ci95": [low, high]}


def expected_calibration_error(probs: np.ndarray, y_true: np.ndarray, bins: int = 15) -> float:
    """Top-label ECE: |accuracy - confidence| per confidence bin, weighted by the bin's share."""
    confidence = probs.max(axis=1)
    predicted = np.asarray(LEVELS)[probs.argmax(axis=1)]
    correct = (predicted == y_true).astype(float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    ece = 0.0
    for low, high in zip(edges[:-1], edges[1:], strict=True):
        in_bin = (confidence > low) & (confidence <= high)
        if in_bin.any():
            ece += in_bin.mean() * abs(correct[in_bin].mean() - confidence[in_bin].mean())
    return float(ece)


def macro_f1(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Macro-F1 over the five levels; a level that is never predicted scores 0."""
    # sklearn is unannotated, so pyright takes the type of `zero_division` from its default "warn".
    zero: Any = 0
    return float(f1_score(y_true, y_pred, labels=list(LEVELS), average="macro", zero_division=zero))


def evaluate(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    *,
    probs_before: np.ndarray | None = None,
    probs_after: np.ndarray | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "n": len(y_true),
        "counts": {str(level): int((y_true == level).sum()) for level in LEVELS},
        "under_triage_l1_2": under_triage_urgent(y_true, y_pred),
        "over_triage": over_triage(y_true, y_pred),
        "macro_f1": macro_f1(y_true, y_pred),
        "accuracy": float((y_true == y_pred).mean()),
        "confusion": confusion_matrix(y_true, y_pred, labels=list(LEVELS)).tolist(),
    }
    if probs_before is not None:
        result["ece_before"] = expected_calibration_error(probs_before, y_true)
    if probs_after is not None:
        result["ece_after"] = expected_calibration_error(probs_after, y_true)
    return result
