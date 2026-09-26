"""Splits, metrics and temperature scaling."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy.special import softmax

from careflow_triage.calibrate import fit_temperature
from careflow_triage.metrics import (
    evaluate,
    expected_calibration_error,
    over_triage,
    under_triage_urgent,
    wilson,
)
from careflow_triage.splits import load_splits, make_splits, save_splits

LABELS = np.repeat(np.arange(1, 6), [26, 220, 487, 459, 75])  # KTAS_expert level counts


def test_splits_are_70_15_15_stratified_and_seeded(tmp_path: Path) -> None:
    splits = make_splits(LABELS)
    sizes = {k: len(v) for k, v in splits.items()}
    assert sizes == {"train": 886, "val": 190, "test": 191}
    assert sorted(splits["train"] + splits["val"] + splits["test"]) == list(range(len(LABELS)))
    assert [int((LABELS[splits["test"]] == level).sum()) for level in range(1, 6)] == [
        4,
        33,
        73,
        69,
        12,
    ]
    assert make_splits(LABELS) == splits and make_splits(LABELS, seed=7) != splits
    path = tmp_path / "s.json"
    save_splits(path, splits, csv_sha256="abc", seed=42, labels=LABELS)
    assert load_splits(path, csv_sha256="abc") == splits
    assert load_splits(path, csv_sha256="other") is None


def test_wilson_interval() -> None:
    low, high = wilson(0, 10)
    assert low == 0.0 and round(high, 4) == 0.2775
    low, high = wilson(9, 37)
    assert round(low, 3) == 0.134 and round(high, 3) == 0.401


def test_under_and_over_triage() -> None:
    y_true = np.array([1, 2, 2, 3, 4, 5])
    y_pred = np.array([2, 2, 1, 3, 3, 5])
    under = under_triage_urgent(y_true, y_pred)
    assert (under["count"], under["of"]) == (1, 3)  # the level 1 predicted as 2
    over = over_triage(y_true, y_pred)
    assert (over["count"], over["of"]) == (2, 6)  # 2 -> 1 and 4 -> 3
    result = evaluate(y_true, y_pred)
    assert result["confusion"][0] == [0, 1, 0, 0, 0] and 0 <= result["macro_f1"] <= 1


def test_ece_is_zero_for_confident_correct_predictions() -> None:
    probs = np.eye(5)[[0, 1, 2]]
    assert expected_calibration_error(probs, np.array([1, 2, 3])) == 0.0
    assert expected_calibration_error(probs, np.array([2, 2, 3])) > 0.3


def test_temperature_scaling_recovers_a_known_temperature() -> None:
    rng = np.random.default_rng(0)
    logits = rng.normal(0, 3, size=(4000, 5))
    true_probs = softmax(logits / 2.0, axis=1)
    targets = np.array([rng.choice(5, p=p) for p in true_probs])
    assert abs(fit_temperature(logits, targets) - 2.0) < 0.15
