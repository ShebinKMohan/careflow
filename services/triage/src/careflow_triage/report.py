"""Reports: eval/reports/triage/ktas_baseline_<YYYYMMDD>.{json,md} and MODEL_CARD.md.

Aggregate metrics only. A report covers every model in `artifacts/` trained on the same CSV and
seed (both baselines once each has been trained). Fixture runs are NOT A RESULT.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from careflow_triage.paths import ARTIFACTS_DIR, MODEL_CARD, REPORTS_DIR

NOT_A_RESULT = "**NOT A RESULT: trained on the synthetic fixture, not on KTAS.**"
DATA = (
    "KTAS (PLOS ONE 2019, doi:10.1371/journal.pone.0216972, CC BY 4.0), 1,267 records of adult "
    "patients (15 and over) in two Korean emergency departments, October 2016 to September 2017"
)


def metas(artifacts_dir: Path, *, csv_sha256: str, seed: int) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for path in sorted(artifacts_dir.glob("*/meta.json")):
        meta = json.loads(path.read_text(encoding="utf-8"))
        if meta["csv_sha256"] == csv_sha256 and meta["seed"] == seed:
            found[meta["kind"]] = meta
    return dict(sorted(found.items()))


def _pct(value: float) -> str:
    return f"{100 * value:.1f}%"


def _ut(block: dict[str, Any]) -> str:
    low, high = block["ci95"]
    return (
        f"{block['count']}/{block['of']} = {_pct(block['rate'])} (95% CI {_pct(low)}-{_pct(high)})"
    )


def markdown(models: dict[str, dict[str, Any]], *, day: str) -> str:
    first = next(iter(models.values()))
    fixture = first["fixture"]
    test = first["metrics"]["test"]
    lines = [
        f"# KTAS baseline urgency models ({day})",
        "",
        NOT_A_RESULT if fixture else f"Data: {DATA}. Target: `KTAS_expert`.",
        "",
        f"CSV SHA-256 `{first['csv_sha256']}`; split `{first['split_file']}` "
        f"(stratified 70/15/15, seed {first['seed']}): "
        + ", ".join(f"{k} {v}" for k, v in first["split_counts"].items())
        + ". Test counts per level (1-5): "
        + ", ".join(str(v) for v in test["counts"].values())
        + ".",
        "",
        "Levels: 1 = most urgent. Under-triage (L1-2) = true level 1-2 predicted less urgent. "
        "Over-triage = any case predicted more urgent. "
        "ECE: top-label, 15 bins, before/after temperature scaling.",
        "",
        "| Test set | Under-triage L1-2 | Over-triage | Macro-F1 | Accuracy "
        "| ECE before | ECE after | T |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for kind, meta in models.items():
        m = meta["metrics"]["test"]
        lines.append(
            f"| {kind} `{meta['model_version']}` | {_ut(m['under_triage_l1_2'])} "
            f"| {_ut(m['over_triage'])} | {m['macro_f1']:.3f} | {m['accuracy']:.3f} "
            f"| {m['ece_before']:.3f} | {m['ece_after']:.3f} | {meta['temperature']:.2f} |"
        )
    nurse = first["metrics"]["nurse_comparator_test"]
    lines.append(
        f"| nurses (`KTAS_RN`), same test rows | {_ut(nurse['under_triage_l1_2'])} "
        f"| {_ut(nurse['over_triage'])} | {nurse['macro_f1']:.3f} | {nurse['accuracy']:.3f} "
        "| n/a | n/a | n/a |"
    )
    lines += [
        "",
        "Temperature scaling (T fitted on val by NLL; val is therefore in-sample):",
        "",
        "| Model | Val NLL | Val ECE | Test NLL | Test ECE |",
        "|---|---|---|---|---|",
    ]
    for kind, meta in models.items():
        cal = meta["metrics"]["calibration"]
        v, t = cal["val"], cal["test"]
        lines.append(
            f"| {kind} (T={meta['temperature']:.2f}) "
            f"| {v['nll_before']:.3f} -> {v['nll_after']:.3f} "
            f"| {v['ece_before']:.3f} -> {v['ece_after']:.3f} "
            f"| {t['nll_before']:.3f} -> {t['nll_after']:.3f} "
            f"| {t['ece_before']:.3f} -> {t['ece_after']:.3f} |"
        )
    everyone = first["metrics"]["nurse_comparator_all"]
    lines += [
        "",
        f"Nurses on all {everyone['n']:,} rows: "
        f"under-triage L1-2 {_ut(everyone['under_triage_l1_2'])}, "
        f"over-triage {_ut(everyone['over_triage'])}, macro-F1 {everyone['macro_f1']:.3f}.",
    ]
    cvs = {kind: meta["metrics"]["cv"] for kind, meta in models.items() if meta["metrics"]["cv"]}
    if cvs:
        lines += [
            "",
            "Level 1 has fewer than 5 test rows, so a stratified 5-fold CV over all rows is "
            "reported too (pooled out-of-fold predictions, uncalibrated):",
            "",
            "| 5-fold CV | Under-triage L1-2 (pooled) | Over-triage (pooled) "
            "| Macro-F1 mean +- SD |",
            "|---|---|---|---|",
        ]
        for kind, cv in cvs.items():
            pooled = cv["pooled"]
            lines.append(
                f"| {kind} | {_ut(pooled['under_triage_l1_2'])} | {_ut(pooled['over_triage'])} "
                f"| {cv['macro_f1_mean']:.3f} +- {cv['macro_f1_sd']:.3f} |"
            )
    lines += ["", "Test confusion matrices (rows: true level 1-5; columns: predicted 1-5):", ""]
    for kind, meta in [*models.items(), ("nurses", {"metrics": {"test": nurse}})]:
        lines.append(f"- {kind}: `{meta['metrics']['test']['confusion']}`")
    lines += [
        "",
        "Caveats: adults in two Korean EDs; these results say nothing about children, UAE or UK "
        "patients. Level 1 has very few cases, so its intervals are wide; no target is claimed as "
        "achieved. The model only suggests a level: the safety rules run first and a nurse "
        "confirms every level.",
        "",
        "Research prototype. Synthetic data only in the product; "
        "this dataset is public research data.",
    ]
    return "\n".join(lines) + "\n"


def write_reports(
    *,
    csv_sha256: str,
    seed: int,
    artifacts_dir: Path = ARTIFACTS_DIR,
    reports_dir: Path = REPORTS_DIR,
    day: date | None = None,
) -> tuple[Path, Path]:
    models = metas(artifacts_dir, csv_sha256=csv_sha256, seed=seed)
    stamp = (day or date.today()).strftime("%Y%m%d")
    prefix = "fixture_" if next(iter(models.values()))["fixture"] else ""
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = reports_dir / f"{prefix}ktas_baseline_{stamp}.json"
    md_path = reports_dir / f"{prefix}ktas_baseline_{stamp}.md"
    body = {
        "not_a_result": next(iter(models.values()))["fixture"],
        "models": {
            kind: {
                k: meta[k]
                for k in (
                    "model_version",
                    "model_id",
                    "params",
                    "grid",
                    "temperature",
                    "metrics",
                    "libraries",
                    "split_counts",
                    "csv_sha256",
                    "seed",
                )
            }
            for kind, meta in models.items()
        },
    }
    json_path.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")
    md_path.write_text(markdown(models, day=stamp), encoding="utf-8")
    return json_path, md_path


def write_model_card(
    *,
    csv_sha256: str,
    seed: int,
    artifacts_dir: Path = ARTIFACTS_DIR,
    path: Path = MODEL_CARD,
    report_name: str = "",
) -> Path:
    models = metas(artifacts_dir, csv_sha256=csv_sha256, seed=seed)
    first = next(iter(models.values()))
    rows = []
    for kind, meta in models.items():
        m = meta["metrics"]["test"]
        rows.append(
            f"| {kind} | `{meta['model_version']}` | {_ut(m['under_triage_l1_2'])} "
            f"| {m['macro_f1']:.3f} | {m['ece_before']:.3f} -> {m['ece_after']:.3f} |"
        )
    fixture = any(meta["fixture"] for meta in models.values())
    level_1 = first["metrics"]["nurse_comparator_all"]["counts"]["1"]
    lines = [
        "# Model card: CareFlow KTAS baseline urgency models",
        "",
        NOT_A_RESULT if fixture else "",
        "",
        "- **What:** logistic regression (`lr`) and HistGradientBoosting (`hgb`) classifiers that "
        "suggest a KTAS urgency level (1 = most urgent) from 14 intake features, calibrated with "
        "temperature scaling. `UrgencyModel.predict(facts)` returns the level, a display label "
        "(KTAS, ESI, CTAS or MTS; a display mapping only, the scales are not clinically "
        "equivalent), a calibrated confidence and the top three SHAP reasons.",
        "- **Intended use:** a suggestion for the triage nurse in a research prototype. The "
        "deterministic safety rules run first and a nurse confirms or overrides every level. "
        "Not for clinical use.",
        "- **Data:** KTAS, Moon et al., PLOS ONE 2019 (doi:10.1371/journal.pone.0216972, "
        "CC BY 4.0): 1,267 randomly selected records of adult patients in two Korean emergency "
        "departments, October 2016 to September 2017 (the article, checked 27 Sep 2026), "
        "labelled by triage experts (`KTAS_expert`). The rows are never committed or "
        "redistributed.",
        "- **Features:** age, sex, arrival mode, injury, ACVPU, pain (yes/no), pain score, SBP, "
        "DBP, heart rate, respiratory rate, temperature, SpO2 and a keyword-mapped complaint "
        "category, all read from `StructuredFacts` by one function (`facts_to_features`), the "
        "same at training and serving. Excluded (leakage): the nurses' and experts' levels, "
        "diagnosis, disposition, error group, length of stay, KTAS duration and mistriage.",
        f"- **Evaluation:** seed {seed}, stratified 70/15/15 split; test set results below "
        f"(full report: `eval/reports/triage/{report_name}`).",
        "",
        "| Model | Version | Under-triage L1-2 (95% CI) | Macro-F1 | ECE before -> after |",
        "|---|---|---|---|---|",
        *rows,
        "",
        "- **Limitations:** adults only (the youngest is 15): a patient known to be under 15 gets "
        "no suggestion (`OutOfScopeError`), and a child whose age is unknown is outside the data. "
        "Two Korean EDs: the results say nothing about UAE or UK patients, other triage systems "
        f"or other hospitals. Level 1 has {level_1} cases in total, so its estimates are very "
        "uncertain; no target is claimed as achieved. SpO2 is missing for one of the two "
        "hospitals. The complaint categories are CareFlow's keyword mapping of short English "
        "text.",
        "- **Ethics and safety:** the model never decides. Rules first (safety rule 1), nurse "
        "confirms (safety rule 3). Laya (M3) must beat this baseline, and every baseline is "
        "published.",
        "",
        "Research prototype. Synthetic data only in the product. Not for clinical use.",
    ]
    text = "\n".join(lines) + "\n"
    path.write_text(text, encoding="utf-8")
    return path
