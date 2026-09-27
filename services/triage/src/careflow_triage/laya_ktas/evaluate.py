"""`careflow-triage laya evaluate`: Laya beside the M1-04 baseline on the test split (M3-01).

Both models are scored once on test (never tuned on it), with M1-04's `metrics.py`, in two views:

- `full`: every recorded vital, as the nurse sees the patient;
- `kiosk_partial`: without respiratory rate, blood pressure and consciousness, as the kiosk sees
  the patient before the nurse (pulse, SpO2 and temperature only).

Rows: under-triage of levels 1-2 with its Wilson 95% interval, over-triage, macro-F1, accuracy,
and the top-label ECE before and after temperature scaling (T fitted on validation only). The
report is written to `eval/reports/triage/laya_ktas_<YYYYMMDD>.{json,md}`, whichever model wins;
anything from the synthetic fixture is `fixture_...` and says NOT A RESULT.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import numpy as np
from scipy.special import softmax

from careflow_contracts import StructuredFacts
from careflow_triage.ktas.features import facts_to_features, row_to_facts
from careflow_triage.ktas.load import load_ktas
from careflow_triage.laya_ktas.calibrate import load_run_model, nll
from careflow_triage.laya_ktas.dataset import encode, load_data
from careflow_triage.laya_ktas.train import TrainConfig, item_logits
from careflow_triage.metrics import evaluate
from careflow_triage.paths import ARTIFACTS_DIR, REPORTS_DIR

VIEWS: Final = ("full", "kiosk_partial")
KIOSK_DROPPED_VITALS: Final = ("respiratory_rate", "systolic_bp", "diastolic_bp", "acvpu")


def kiosk_facts(facts: StructuredFacts) -> StructuredFacts:
    """The facts without the vitals the kiosk cannot measure."""
    if facts.vitals is None:
        return facts
    vitals = facts.vitals.model_copy(update={name: None for name in KIOSK_DROPPED_VITALS})
    return facts.model_copy(update={"vitals": vitals})


def baseline_metrics(artifact_dir: Path, csv_path: Path, test_idx: list[int]) -> dict[str, Any]:
    """The M1-04 model's test metrics in both views (T from its own validation fit)."""
    from careflow_triage.inference import UrgencyModel

    model = UrgencyModel.load(artifact_dir)
    frame = load_ktas(csv_path)
    rows = frame.to_dict("records")
    y = np.array([int(rows[i]["KTAS_expert"]) for i in test_idx])
    classes = np.asarray(model.meta["classes"])
    temperature = float(model.meta["temperature"])
    views: dict[str, Any] = {}
    for view in VIEWS:
        facts = [row_to_facts(rows[i], index=i) for i in test_idx]
        if view == "kiosk_partial":
            facts = [kiosk_facts(f) for f in facts]
        X = np.vstack([facts_to_features(f) for f in facts])
        logits = np.atleast_2d(model.model.decision_function(X))
        predicted = classes[logits.argmax(axis=1)]
        views[view] = evaluate(
            y,
            predicted,
            probs_before=softmax(logits, axis=1),
            probs_after=softmax(logits / temperature, axis=1),
        )
        targets = np.array([list(classes).index(level) for level in y])
        views[view]["nll_before"] = nll(logits, targets, 1.0)
        views[view]["nll_after"] = nll(logits, targets, temperature)
    return {"model_version": model.model_version, "temperature": temperature, "test": views}


def laya_metrics(run_dir: Path, csv_path: Path, config_path: Path) -> dict[str, Any]:
    import torch  # type: ignore[import-not-found]

    config = TrainConfig.load(config_path)
    device = torch.device(config.device if torch.backends.mps.is_available() else "cpu")
    data = load_data(csv_path)
    model, tok, cfg = load_run_model(run_dir, device)
    temperature = float(cfg["temperature"][0])
    test = data.part("test")
    y = np.array([e.level for e in test])
    views: dict[str, Any] = {}
    for view in VIEWS:
        inputs = [e.laya_input if view == "full" else e.laya_input.kiosk_partial() for e in test]
        items = [encode(tok, cfg, x, e.level) for x, e in zip(inputs, test, strict=True)]
        logits = item_logits(model, items, device, tok.pad_token_id, config.eval_batch)
        views[view] = evaluate(
            y,
            logits.argmax(axis=1) + 1,
            probs_before=softmax(logits, axis=1),
            probs_after=softmax(logits / temperature, axis=1),
        )
        views[view]["nll_before"] = nll(logits, y - 1, 1.0)
        views[view]["nll_after"] = nll(logits, y - 1, temperature)
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    return {
        "run_id": meta["run_id"],
        "fixture": data.fixture,
        "csv_sha256": data.csv_sha256,
        "temperature": temperature,
        "calibration": meta.get("calibration"),
        "best_epoch": meta.get("best_epoch"),
        "history": meta.get("history"),
        "train_seconds": meta.get("train_seconds"),
        "test_rows": len(test),
        "test": views,
    }


def _row(name: str, view: str, m: dict[str, Any]) -> str:
    under = m["under_triage_l1_2"]
    low, high = under["ci95"]
    return (
        f"| {name} | {view} | {under['count']}/{under['of']} ({under['rate']:.1%}; "
        f"95% CI {low:.1%}-{high:.1%}) | {m['over_triage']['rate']:.1%} | {m['macro_f1']:.3f} | "
        f"{m['accuracy']:.3f} | {m['ece_before']:.3f} -> {m['ece_after']:.3f} |"
    )


def cross_check(base: dict[str, Any]) -> str:
    published = base.get("m1_04_report")
    if not published:
        return "The baseline rows are recomputed from its artifact; no M1-04 report names it."
    ours = base["test"]["full"]
    same = abs(published["macro_f1"] - ours["macro_f1"]) < 1e-9 and (
        published["under_triage_l1_2"]["count"] == ours["under_triage_l1_2"]["count"]
    )
    verdict = "matches" if same else "DIFFERS FROM"
    return (
        f"The baseline's `full` row is recomputed from its artifact and {verdict} M1-04's report "
        f"`{published['report']}` (macro-F1 {published['macro_f1']:.3f}, under-triage "
        f"{published['under_triage_l1_2']['count']}/{published['under_triage_l1_2']['of']})."
    )


def markdown(report: dict[str, Any]) -> str:
    laya, base = report["laya"], report["baseline"]
    cal = laya.get("calibration") or {}
    nan = float("nan")
    clamped = " (clamped by Laya to [0.5, 5.0])" if cal.get("clamped") else ""
    lines = [
        "# Laya (laya-ktas) beside the M1-04 baseline",
        "",
    ]
    if report["not_a_result"]:
        lines += ["**NOT A RESULT: trained and scored on the synthetic fixture.**", ""]
    lines += [
        f"Test split: {laya['test_rows']} rows of `splits/ktas_v1.json` (seed 42), scored once. "
        f"Laya run `{laya['run_id']}` (best epoch {laya['best_epoch']}); baseline "
        f"`{base['model_version']}`.",
        "",
        "`full`: every recorded vital. `kiosk_partial`: no respiratory rate, blood pressure or "
        "consciousness (the kiosk has pulse, SpO2 and temperature).",
        "",
        "| Model | View | Under-triage L1-2 | Over-triage | Macro-F1 | Accuracy "
        "| ECE before -> after |",
        "|---|---|---|---|---|---|---|",
    ]
    for view in VIEWS:
        lines.append(_row("Laya (laya-ktas)", view, laya["test"][view]))
        lines.append(_row(f"Baseline ({base['model_version']})", view, base["test"][view]))
    lines += [
        "",
        "## Calibration",
        "",
        f"Laya: one `choice` temperature fitted on validation only ({cal.get('n', '?')} scored "
        f"rows: the validation split with all vitals and kiosk-partial). Fitted T = "
        f"{cal.get('temperature_fitted', nan):.3f}; used T = {laya['temperature']:.3f}"
        f"{clamped}. Validation NLL {cal.get('val_nll_before', nan):.3f} -> "
        f"{cal.get('val_nll_after', nan):.3f}.",
        f"Baseline: T = {base['temperature']:.3f} (M1-04, fitted on validation).",
        "",
        "Test NLL before -> after temperature scaling (the quantity the fit minimises; ECE uses "
        f"15 bins over {laya['test_rows']} rows and is noisy):",
        "",
        *[
            f"- {name}, {view}: {m['nll_before']:.3f} -> {m['nll_after']:.3f}"
            for name, block in (("Laya", laya), ("Baseline", base))
            for view in VIEWS
            for m in [block["test"][view]]
        ],
        "",
        cross_check(base),
        "",
        "## Notes",
        "",
        "- Only a suggestion: the rules run first and a nurse confirms every level. Reasons are "
        "shown to staff only.",
        "- 1,267 rows in all: the intervals are wide. Laya may lose to the baseline; it is "
        "reported either way. No number here is a clinical claim.",
    ]
    return "\n".join(lines) + "\n"


def m1_04_report(model_version: str) -> dict[str, Any] | None:
    """The baseline's test metrics as M1-04's newest report published them (a cross-check of the
    recomputed `full` row), or None."""
    for path in sorted(REPORTS_DIR.glob("ktas_baseline_*.json"), reverse=True):
        body = json.loads(path.read_text(encoding="utf-8"))
        for model in body.get("models", {}).values():
            if model.get("model_version") == model_version:
                test = model["metrics"]["test"]
                return {
                    "report": path.name,
                    "macro_f1": test["macro_f1"],
                    "under_triage_l1_2": test["under_triage_l1_2"],
                    "ece_after": test.get("ece_after"),
                }
    return None


def write_report(
    run_dir: Path,
    csv_path: Path,
    config_path: Path,
    *,
    baseline_dir: Path | None = None,
    today: str | None = None,
) -> tuple[Path, Path]:
    laya = laya_metrics(run_dir, csv_path, config_path)
    data = load_data(csv_path)
    baseline_dir = baseline_dir or newest_baseline(fixture=laya["fixture"])
    base = baseline_metrics(baseline_dir, csv_path, data.splits["test"])
    base["m1_04_report"] = m1_04_report(base["model_version"])
    report = {"not_a_result": laya["fixture"], "laya": laya, "baseline": base}
    stamp = today or datetime.now(UTC).strftime("%Y%m%d")
    stem = f"{'fixture_' if laya['fixture'] else ''}laya_ktas_{stamp}"
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path, md_path = REPORTS_DIR / f"{stem}.json", REPORTS_DIR / f"{stem}.md"
    json_path.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    md_path.write_text(markdown(report), encoding="utf-8")
    return json_path, md_path


def newest_baseline(*, fixture: bool) -> Path:
    """The M1-04 HGB artifact (the served baseline) for this data."""
    runs = sorted(
        (p for p in ARTIFACTS_DIR.glob("*ktas-hgb-*") if (p / "model.joblib").exists()),
        key=lambda p: p.stat().st_mtime,
    )
    runs = [p for p in runs if p.name.startswith("fixture-") == fixture]
    if not runs:
        raise FileNotFoundError("no M1-04 hgb artifact: run `careflow-triage train --model hgb`")
    return runs[-1]


__all__ = [
    "KIOSK_DROPPED_VITALS",
    "VIEWS",
    "baseline_metrics",
    "kiosk_facts",
    "laya_metrics",
    "markdown",
    "newest_baseline",
    "write_report",
]
