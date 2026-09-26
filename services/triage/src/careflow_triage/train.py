"""Train the logistic-regression (lr) and HistGradientBoosting (hgb) baselines on KTAS.

Both models see the same features (`facts_to_features`), the same fixed splits and the same seed,
are calibrated with temperature scaling on validation, and are scored once on test. With fewer
than 5 test rows in any level, a pooled stratified 5-fold CV is reported as well. Anything
trained on the synthetic fixture is labelled `fixture-*`: NOT A RESULT.
"""

from __future__ import annotations

import json
import platform
import time
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any, Final, Literal

import joblib
import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from careflow_triage.calibrate import fit_temperature, nll, probabilities
from careflow_triage.ktas.features import (
    CATEGORICAL,
    FEATURE_NAMES,
    facts_to_features,
    row_to_facts,
)
from careflow_triage.ktas.load import file_sha256, load_ktas
from careflow_triage.metrics import LEVELS, evaluate, macro_f1
from careflow_triage.paths import ARTIFACTS_DIR, FIXTURE_CSV, SPLITS_DIR
from careflow_triage.splits import SEED, load_splits, make_splits, save_splits

type ModelKind = Literal["lr", "hgb"]

HGB_GRID: Final = tuple(
    {"learning_rate": rate, "max_leaf_nodes": leaves} for rate in (0.05, 0.1) for leaves in (15, 31)
)
BACKGROUND_ROWS: Final = 100


@dataclass(frozen=True)
class Dataset:
    X: np.ndarray
    y: np.ndarray  # KTAS_expert
    y_nurse: np.ndarray  # KTAS_RN
    csv_sha256: str
    fixture: bool


def load_dataset(csv_path: Path) -> Dataset:
    frame = load_ktas(csv_path)
    rows = frame.to_dict("records")
    X = np.vstack([facts_to_features(row_to_facts(row, index=i)) for i, row in enumerate(rows)])
    return Dataset(
        X=X,
        y=frame["KTAS_expert"].to_numpy(dtype=int),
        y_nurse=frame["KTAS_RN"].to_numpy(dtype=int),
        csv_sha256=file_sha256(csv_path),
        fixture=csv_path.name == FIXTURE_CSV.name,
    )


def build_lr() -> Pipeline:
    numeric = [i for i, name in enumerate(FEATURE_NAMES) if name not in CATEGORICAL]
    categorical = [i for i, name in enumerate(FEATURE_NAMES) if name in CATEGORICAL]
    impute_scale = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median", add_indicator=True)),
            ("scale", StandardScaler()),
        ]
    )
    pre = ColumnTransformer(
        [
            ("num", impute_scale, numeric),
            ("cat", OneHotEncoder(handle_unknown="ignore"), categorical),
        ]
    )
    classifier = LogisticRegression(class_weight="balanced", max_iter=2000)
    return Pipeline([("pre", pre), ("model", classifier)])


def build_hgb(**params: Any) -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        class_weight="balanced", random_state=SEED, max_iter=200, **params
    )


def _targets(model: Any, labels: np.ndarray) -> np.ndarray:
    classes = list(model.classes_)
    return np.array([classes.index(label) for label in labels])


def fit(
    kind: ModelKind, X_train: np.ndarray, y_train: np.ndarray, X_val: np.ndarray, y_val: np.ndarray
) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
    """The model, its parameters and (hgb) the validation grid it was chosen from."""
    if kind == "lr":
        return build_lr().fit(X_train, y_train), {"C": 1.0}, []
    grid: list[dict[str, Any]] = []
    best: tuple[float, float, int] | None = None
    chosen: Any = None
    for i, params in enumerate(HGB_GRID):
        model = build_hgb(**params).fit(X_train, y_train)
        pred = model.predict(X_val)
        f1 = macro_f1(y_val, pred)
        loss = nll(model.decision_function(X_val), _targets(model, y_val), 1.0)
        grid.append({**params, "val_macro_f1": f1, "val_nll": loss})
        key = (-f1, loss, i)
        if best is None or key < best:
            best, chosen = key, (model, params)
    if chosen is None:
        raise ValueError("HGB_GRID is empty")
    return chosen[0], dict(chosen[1]), grid


def cross_validate(
    kind: ModelKind, params: dict[str, Any], data: Dataset, *, seed: int
) -> dict[str, Any]:
    """Pooled out-of-fold predictions of a stratified 5-fold CV (uncalibrated argmax)."""
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=seed)
    predicted = np.zeros_like(data.y)
    per_fold: list[float] = []
    for train_idx, test_idx in folds.split(data.X, data.y):
        model = build_lr() if kind == "lr" else build_hgb(**params)
        model.fit(data.X[train_idx], data.y[train_idx])
        predicted[test_idx] = model.predict(data.X[test_idx])
        per_fold.append(macro_f1(data.y[test_idx], predicted[test_idx]))
    pooled = evaluate(data.y, predicted)
    return {
        "folds": 5,
        "macro_f1_per_fold": per_fold,
        "macro_f1_mean": float(np.mean(per_fold)),
        "macro_f1_sd": float(np.std(per_fold)),
        "pooled": pooled,
    }


def model_version(kind: ModelKind, data: Dataset, seed: int) -> str:
    return f"{'fixture-' if data.fixture else ''}ktas-{kind}-{data.csv_sha256[:8]}-s{seed}"


@dataclass(frozen=True)
class TrainResult:
    model_version: str
    artifact_dir: Path
    meta: dict[str, Any]


def train(
    kind: ModelKind,
    csv_path: Path,
    *,
    seed: int = SEED,
    artifacts_dir: Path = ARTIFACTS_DIR,
    splits_path: Path = SPLITS_DIR / "ktas_v1.json",
) -> TrainResult:
    started = time.perf_counter()
    data = load_dataset(csv_path)
    splits = load_splits(splits_path, csv_sha256=data.csv_sha256)
    if splits is None:
        splits = make_splits(data.y, seed=seed)
        save_splits(splits_path, splits, csv_sha256=data.csv_sha256, seed=seed, labels=data.y)
    tr, va, te = (np.array(splits[name]) for name in ("train", "val", "test"))
    model, params, grid = fit(kind, data.X[tr], data.y[tr], data.X[va], data.y[va])
    classes = [int(c) for c in model.classes_]
    temperature = fit_temperature(model.decision_function(data.X[va]), _targets(model, data.y[va]))
    logits = model.decision_function(data.X[te])
    predicted = np.asarray(classes)[logits.argmax(axis=1)]
    test_metrics = evaluate(
        data.y[te],
        predicted,
        probs_before=probabilities(logits),
        probs_after=probabilities(logits, temperature),
    )
    test_counts = [int((data.y[te] == level).sum()) for level in LEVELS]
    calibration = {
        name: calibration_check(model, data.X[idx], data.y[idx], temperature)
        for name, idx in (("val", va), ("test", te))
    }
    cv = cross_validate(kind, params, data, seed=seed) if min(test_counts) < 5 else None
    version = model_version(kind, data, seed)
    artifact_dir = artifacts_dir / version
    artifact_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, artifact_dir / "model.joblib")
    np.save(artifact_dir / "background.npy", data.X[tr][:BACKGROUND_ROWS])
    meta: dict[str, Any] = {
        "model_version": version,
        "model_id": f"careflow-ktas-{kind}",
        "kind": kind,
        "fixture": data.fixture,
        "features": list(FEATURE_NAMES),
        "categorical": sorted(CATEGORICAL),
        "classes": classes,
        "temperature": temperature,
        "params": params,
        "grid": grid,
        "seed": seed,
        "csv_sha256": data.csv_sha256,
        "split_file": str(splits_path.name),
        "split_counts": {name: len(idx) for name, idx in splits.items()},
        "metrics": {
            "test": test_metrics,
            "nurse_comparator_test": evaluate(data.y[te], data.y_nurse[te]),
            "nurse_comparator_all": evaluate(data.y, data.y_nurse),
            "cv": cv,
            "calibration": calibration,
        },
        "libraries": {
            name: version_of(name) for name in ("scikit-learn", "numpy", "scipy", "shap", "pandas")
        },
        "python": platform.python_version(),
        "train_seconds": round(time.perf_counter() - started, 2),
    }
    (artifact_dir / "meta.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    return TrainResult(version, artifact_dir, meta)


def calibration_check(
    model: Any, X: np.ndarray, y: np.ndarray, temperature: float
) -> dict[str, float]:
    """NLL and ECE before and after temperature scaling.

    T is fitted on val, so val is in-sample.
    """
    from careflow_triage.metrics import expected_calibration_error

    logits = model.decision_function(X)
    targets = _targets(model, y)
    return {
        "nll_before": nll(logits, targets, 1.0),
        "nll_after": nll(logits, targets, temperature),
        "ece_before": expected_calibration_error(probabilities(logits), y),
        "ece_after": expected_calibration_error(probabilities(logits, temperature), y),
    }


def version_of(package: str) -> str:
    return version(package)
