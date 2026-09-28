"""The per-language extraction report (M2-03): `eval/reports/extraction.json` and `.md`.

It combines the English (`extraction_en`) and Arabic (`extraction_ar`) suite reports. Missed
expected alerts come first, by scenario id, and the report says so when there are none (a missed
Arabic fact is a missed alert: safety rule 1). Then field precision, recall and F1 per language
and per Arabic dialect, per field, the span-verified rate, the models and the caveats.

Arabic numbers here are technical measurements of one recorded local run on synthetic scenarios
that no native speaker validated. The report is `publish: false` (M6-01), and its numbers are
never shown as an accuracy percentage, in the UI, the README or anywhere else.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from careflow_eval.metrics.alerts import alert_counts
from careflow_eval.metrics.extraction import field_counts
from careflow_eval.suites.extraction_en import presence_scored

LANGUAGE_SUITES: Final = {"en": "extraction_en", "ar": "extraction_ar"}
DIALECTS: Final = ("gulf", "levantine")
REPO: Final = Path(__file__).resolve().parents[3]
AGENT_REVIEW: Final = REPO / "eval" / "datasets" / "intake_ar" / "AGENT_REVIEW.json"
# The optional Jais comparison (M2-03 step 7), checked on Hugging Face on 28 September 2026.
OPTIONAL_MODELS: Final = [
    {
        "model": "Jais 2 8B Chat",
        "language": "ar",
        "status": "skipped",
        "reason": (
            "No ungated pre-converted mlx-community 4-bit Jais 2 build exists (Hugging Face "
            "search, 28 Sep 2026). The official inceptionai/Jais-2-8B-Chat (and its "
            "inception42 mirror) is gated: accepting its terms is an owner decision. mlx-lm "
            "0.31.3 has no Jais2ForCausalLM architecture, and converting the bf16 weights needs "
            "about 16 GB RAM (skipped: RAM)."
        ),
    }
]
CAVEATS: Final = [
    "Synthetic scenarios only. The gold facts were sampled in code; the patient answers were "
    "written by a coding agent, and each scenario was reviewed by an automated model of another "
    "family, not by clinicians.",
    "The Arabic was not validated by native speakers. Each Arabic answer and its English gloss "
    "were checked only by machine: a blind back-translation, a model judgement and chrF >= 40 "
    "(the M2-01 check), then a local 9B model's review of the whole scenario.",
    "Arabic free-text facts (complaint, times, places, allergies, symptoms) are scored by "
    "presence only, because the extractor writes them as English renderings; yes/no/unknown "
    "and numeric facts are scored exactly, as in English. English free text is scored by exact "
    "value, so the English and Arabic field scores are not comparable.",
    "The eval measures the extractor alone, on the whole transcript. In the intake graph an "
    "explicit yes, no or not-sure reply to the question on screen is read first without a model "
    "(ADR-052), and an unsure reading beats a model's no, so a missed alert listed here from an "
    "unsure reply is kept as unknown there.",
    "These are technical measurements of one recorded local run (Qwen3.5-9B 4-bit), not "
    "clinical accuracy. Not for publication (publish: false). No Arabic accuracy percentage "
    "may appear in the UI, the README or any public material.",
]


def _completed(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [row for row in rows if row.get("status") == "completed"]


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def summarise(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Field and alert scores over the completed cases, and the span counts."""
    completed = _completed(rows)
    predictions = [row["prediction"] for row in completed]
    expected = [row["expected"] for row in completed]
    fields = field_counts(predictions, expected)
    alerts = alert_counts(predictions, expected)
    spans = [row["spans"] for row in completed if isinstance(row.get("spans"), Mapping)]
    kept = sum(span["kept"] for span in spans)
    dropped = sum(span["dropped"] for span in spans)
    stated = sum(span["patient_stated"] for span in spans)
    sliced = sum(span["sliced"] for span in spans)
    return {
        "cases": len(rows),
        "completed": len(completed),
        "incomplete_extractions": sum(
            bool(row["prediction"].get("extraction_incomplete")) for row in completed
        ),
        "field_precision": fields["precision"],
        "field_recall": fields["recall"],
        "field_f1": fields["f1"],
        "tp": fields["tp"],
        "fp": fields["fp"],
        "fn": fields["fn"],
        "alert_recall": alerts["recall"],
        "expected_alerts": alerts["expected_alerts"],
        "missed_alerts": alerts["missed_alerts"],
        "false_alert_rate": alerts["false_alert_rate"],
        # Kept facts over kept + dropped (dropped: an unverified span or number, a key outside
        # the extractor's scope, a value that does not fit).
        "span_verified_rate": _rate(kept, kept + dropped),
        "facts_kept": kept,
        "facts_dropped": dropped,
        # Accepted PATIENT_STATED facts whose span slices, in the original turn, to the quote.
        "patient_stated_slice_rate": _rate(sliced, stated),
        "patient_stated_facts": stated,
        "patient_stated_sliced": sliced,
    }


def missed_alerts(language: str, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every scenario with an expected alert the rules did not raise on the extracted facts,
    including a case that was not run (it raised nothing)."""
    missed = []
    for row in rows:
        expected = set(row.get("expected", {}).get("alerts", []))
        prediction = row.get("prediction") or {}
        raised = set(prediction.get("alerts", [])) if row.get("status") == "completed" else set()
        if expected - raised:
            missed.append(
                {
                    "id": row["id"],
                    "language": language,
                    "dialect": row.get("dialect"),
                    "category": row.get("category"),
                    "missed": sorted(expected - raised),
                    "raised": sorted(raised),
                    "status": row.get("status"),
                    "error_code": row.get("error_code"),
                    "extraction_incomplete": prediction.get("extraction_incomplete"),
                }
            )
    return missed


def incomplete_cases(language: str, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": row["id"],
            "language": language,
            "dialect": row.get("dialect"),
            "status": row.get("status"),
            "error_code": row.get("error_code") or row.get("reason"),
        }
        for row in rows
        if row.get("status") != "completed"
        or (row.get("prediction") or {}).get("extraction_incomplete")
    ]


def _model(language: str, report: Mapping[str, Any]) -> dict[str, Any]:
    metadata = report.get("metadata") or {}
    receipt = metadata.get("model_receipt") or {}
    return {
        "model": metadata.get("logical_model"),
        "model_id": receipt.get("model_id"),
        "revision": receipt.get("revision"),
        "provider": metadata.get("provider"),
        "language": language,
        "prompt_version": metadata.get("prompt_version"),
        "mode": metadata.get("mode"),
        "status": "run",
    }


def build(
    reports: Mapping[str, Mapping[str, Any]],
    *,
    agent_review: Mapping[str, Any] | None = None,
    sources: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The combined report for the languages in `reports` ({"en": ..., "ar": ...})."""
    if not reports or set(reports) - set(LANGUAGE_SUITES):
        raise ValueError(f"reports must be keyed by {sorted(LANGUAGE_SUITES)}")
    missed: list[dict[str, Any]] = []
    incomplete: list[dict[str, Any]] = []
    by_language: dict[str, Any] = {}
    per_field: dict[str, Any] = {}
    for language in (lang for lang in LANGUAGE_SUITES if lang in reports):
        report = reports[language]
        if report.get("suite") != LANGUAGE_SUITES[language]:
            raise ValueError(f"{language}: expected a {LANGUAGE_SUITES[language]} report")
        rows = report.get("results") or []
        missed.extend(missed_alerts(language, rows))
        incomplete.extend(incomplete_cases(language, rows))
        by_language[language] = summarise(rows)
        completed = _completed(rows)
        keys = field_counts(
            [row["prediction"] for row in completed], [row["expected"] for row in completed]
        )["per_key"]
        per_field[language] = {
            key: {
                **scores,
                "scored_by": "presence"
                if language == "ar" and presence_scored(key)
                else "exact value",
            }
            for key, scores in keys.items()
        }
        if language == "ar":
            for dialect in DIALECTS:
                subset = [row for row in rows if row.get("dialect") == dialect]
                by_language[f"ar_{dialect}"] = summarise(subset)
    return {
        "schema_version": 1,
        "report": "extraction by language (M2-03)",
        "publish": False,
        "missed_alerts": missed,
        "missed_alerts_note": (
            "Every expected alert was raised."
            if not missed
            else f"{len(missed)} scenario(s) with a missed expected alert."
        ),
        "incomplete_cases": incomplete,
        "by_language": by_language,
        "per_field": per_field,
        "span_verified_rate": {
            language: summary["span_verified_rate"] for language, summary in by_language.items()
        },
        "patient_stated_slice_rate": {
            language: summary["patient_stated_slice_rate"]
            for language, summary in by_language.items()
        },
        "models": [
            *(_model(language, reports[language]) for language in reports),
            *(model for model in OPTIONAL_MODELS if model["language"] in reports),
        ],
        "caveats": CAVEATS,
        "agent_review": dict(agent_review) if agent_review is not None else None,
        "sources": {
            language: {
                "suite": reports[language].get("suite"),
                "status": reports[language].get("status"),
                "mode": (reports[language].get("metadata") or {}).get("mode"),
                "dataset_sha256": (reports[language].get("metadata") or {}).get("dataset_sha256"),
                "report": (sources or {}).get(language),
            }
            for language in reports
        },
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def load_agent_review(path: Path = AGENT_REVIEW) -> dict[str, Any] | None:
    """The agent's notes on 10 random Arabic scenarios, if written."""
    if not path.is_file():
        return None
    body: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return body


def _number(value: Any) -> str:
    """A score as a decimal fraction, never a percentage."""
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _cell(value: Any) -> str:
    return str(value if value is not None else "n/a").replace("|", "\\|").replace("\n", " ")


def markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Extraction by language (M2-03)",
        "",
        "Technical report, not for publication (`publish: false`). Synthetic scenarios only; the "
        "Arabic was not validated by native speakers. Scores are decimal fractions from one "
        "recorded local run, not clinical or translation accuracy.",
        "",
        "## Missed expected alerts",
        "",
    ]
    missed = report["missed_alerts"]
    if missed:
        lines += [
            "| Scenario | Language | Dialect | Missed | Raised | Status | Error |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for row in missed:
            cells = [
                row["id"],
                row["language"],
                row["dialect"],
                ", ".join(row["missed"]),
                ", ".join(row["raised"]) or "none",
                row["status"],
                row["error_code"],
            ]
            lines.append("| " + " | ".join(_cell(cell) for cell in cells) + " |")
    else:
        lines.append("None: every expected alert was raised on the extracted facts.")
    lines += ["", "## Incomplete extractions", ""]
    incomplete = report["incomplete_cases"]
    lines += [
        f"- `{row['id']}` ({row['language']}{', ' + row['dialect'] if row['dialect'] else ''}): "
        f"{row['status']}, {row['error_code'] or 'extraction incomplete'}"
        for row in incomplete
    ] or ["None."]
    lines += [
        "",
        "## By language",
        "",
        "| Language | Cases | Completed | Field precision | Field recall | Field F1 | "
        "Alert recall | False-alert rate | Span-verified rate | PATIENT_STATED slices |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for language, row in report["by_language"].items():
        cells = [
            language,
            row["cases"],
            row["completed"],
            _number(row["field_precision"]),
            _number(row["field_recall"]),
            _number(row["field_f1"]),
            _number(row["alert_recall"]),
            _number(row["false_alert_rate"]),
            _number(row["span_verified_rate"]),
            f"{_number(row['patient_stated_slice_rate'])} ({row['patient_stated_sliced']} of "
            f"{row['patient_stated_facts']})",
        ]
        lines.append("| " + " | ".join(_cell(cell) for cell in cells) + " |")
    lines += [
        "",
        "Arabic free-text facts are scored by presence and English ones by exact value, so the "
        "two languages' field scores are not comparable. Coded and numeric facts are exact in "
        "both.",
    ]
    for language, fields in report["per_field"].items():
        lines += [
            "",
            f"## Per field: {language}",
            "",
            "| Field | Scored by | TP | FP | FN | Precision | Recall | F1 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for key, row in fields.items():
            cells = [
                f"`{key}`",
                row["scored_by"],
                row["tp"],
                row["fp"],
                row["fn"],
                _number(row["precision"]),
                _number(row["recall"]),
                _number(row["f1"]),
            ]
            lines.append("| " + " | ".join(_cell(cell) for cell in cells) + " |")
    lines += ["", "## Models", ""]
    for model in report["models"]:
        detail = model.get("reason") or (
            f"{model.get('model_id')}@{model.get('revision')}, prompt "
            f"{model.get('prompt_version')}, {model.get('mode')}"
        )
        lines.append(f"- {model['model']} ({model['language']}): {model['status']}. {detail}")
    review = report.get("agent_review")
    lines += ["", "## Agent review of 10 random Arabic scenarios", ""]
    if review:
        lines += [f"*{review['label']}.* Sampled with seed {review['seed']}.", ""]
        lines += [f"- `{note['id']}`: {note['note']}" for note in review["notes"]]
        lines += ["", review.get("summary", "")]
    else:
        lines.append("Not written.")
    lines += ["", "## Caveats", ""]
    lines += [f"- {caveat}" for caveat in report["caveats"]]
    return "\n".join(lines).rstrip() + "\n"


def write(report: Mapping[str, Any], root: Path) -> tuple[Path, Path]:
    """Replace `root/extraction.json` and `.md` atomically (the current combined report)."""
    root.mkdir(parents=True, exist_ok=True)
    paths = (root / "extraction.json", root / "extraction.md")
    texts = (
        json.dumps(dict(report), indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        markdown(report),
    )
    for path, text in zip(paths, texts, strict=True):
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=root, delete=False, suffix=".tmp"
        ) as handle:
            handle.write(text)
            staged = Path(handle.name)
        try:
            os.replace(staged, path)
        finally:
            staged.unlink(missing_ok=True)
    return paths


__all__ = [
    "CAVEATS",
    "LANGUAGE_SUITES",
    "OPTIONAL_MODELS",
    "build",
    "incomplete_cases",
    "load_agent_review",
    "markdown",
    "missed_alerts",
    "summarise",
    "write",
]
