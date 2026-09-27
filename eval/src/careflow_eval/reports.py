"""Deterministic, attributable evaluation reports; never a clinical validation claim."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ReportPaths:
    json_path: Path
    markdown_path: Path
    latest_path: Path


def _cell(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return (
        str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\r", "").replace("\n", "<br>")
    )


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    return [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows),
    ]


def _label(name: str) -> str:
    if name in {"accuracy", "expected_level_accuracy", "triage_accuracy"}:
        return "Synthetic scenario label agreement (not clinical accuracy)"
    return name.replace("_", " ")


def _breakdowns(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    tables: list[tuple[str, Any]] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if key in {"per_key", "per_rule"}:
                tables.append((path, child))
            else:
                tables.extend(_breakdowns(child, path))
    return tables


def _breakdown_table(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        rows = [
            {"key": key, **(dict(item) if isinstance(item, Mapping) else {"value": item})}
            for key, item in sorted(value.items())
        ]
    elif isinstance(value, list) and all(isinstance(item, Mapping) for item in value):
        rows = value
    else:
        return [_cell(value)]
    if not rows:
        return ["n/a"]
    columns = sorted({str(key) for row in rows for key in row})
    for preferred in ("rule_id", "key"):
        if preferred in columns:
            columns.remove(preferred)
            columns.insert(0, preferred)
    return _table(columns, [[row.get(column) for column in columns] for row in rows])


def failing_scenario_ids(report: Mapping[str, Any]) -> list[str]:
    found: set[str] = set()

    def visit(value: Any, key: str = "") -> None:
        if key in {"failing_ids", "failed_ids", "failing_scenario_ids", "failed_scenario_ids"}:
            if isinstance(value, list):
                found.update(str(item) for item in value)
        elif isinstance(value, Mapping):
            for child_key, child in value.items():
                visit(child, str(child_key))
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(report.get("metrics", {}))
    visit(report.get("failing_ids", []), "failing_ids")
    visit(report.get("failed_ids", []), "failed_ids")
    for result in report.get("results", []):
        if not isinstance(result, Mapping):
            continue
        prediction = result.get("prediction") or {}
        if (
            result.get("status") in {"not_run", "failed", "error"}
            or result.get("passed") is False
            or result.get("quality_passed") is False
            or result.get("rules_validation") is False
            or result.get("error")
            or result.get("error_code")
            or result.get("failures")
            or (isinstance(prediction, Mapping) and prediction.get("extraction_incomplete"))
        ):
            if identifier := result.get("id", result.get("scenario_id")):
                found.add(str(identifier))
    return sorted(found)


def render_markdown(report: Mapping[str, Any]) -> str:
    metadata = report.get("metadata") or {}
    metrics = report.get("metrics") or {}
    results = report.get("results") or []
    lines = [
        f"# CareFlow {_cell(report.get('suite', 'evaluation'))} evaluation",
        "",
        "Research prototype. Synthetic data only. Not for clinical use.",
        "",
        "Scenario review is automated, not performed by clinicians or native speakers.",
        "Expected urgency levels are synthetic scenario labels; "
        "their agreement is not clinical triage accuracy.",
        "A completed run describes execution, not a clinical pass or acceptable model quality.",
        "",
        f"**Run status:** {_cell(report.get('status'))}",
        "",
        "## Provenance",
        "",
    ]
    required_metadata = (
        "git_sha",
        "dataset_sha256",
        "mode",
        "provider",
        "logical_model",
        "model_receipt",
        "prompt_version",
        "prompt_sha256",
        "ruleset_version",
        "latency_source",
    )
    keys = sorted(set(required_metadata) | set(metadata))
    lines.extend(_table(["Field", "Recorded value"], [[key, metadata.get(key)] for key in keys]))
    if "replay" in str(metadata.get("mode", "")).lower():
        lines.extend(
            [
                "",
                "For successful model calls, this run replays recorded raw model responses "
                "through the canonical `ReplayProvider`."
                if report.get("suite") == "extraction_en"
                else "This run replays recorded rules outputs without invoking a model.",
                "Recorded live latency and replay execution time are distinct; "
                "use the stated latency source.",
            ]
        )
        timeout_count = metadata.get("failure_event_replay_count") or sum(
            1
            for result in results
            if isinstance(result, Mapping) and result.get("failure_event_replayed") is True
        )
        if timeout_count:
            lines.extend(
                [
                    "",
                    f"This run also replays {_cell(timeout_count)} confirmed timeout event(s).",
                    "A confirmed terminal timeout is reproduced by immediately raising "
                    "`TimeoutError` through the production extractor. It is an observed failure "
                    "event, not a recorded model reply or a new live timeout measurement.",
                    "Ordered provider calls and the resulting extraction/rules output hash are "
                    "checked. Historical case latency is retained separately "
                    "from replay wall time.",
                    "The original `recording_replayable: false` remains unchanged: it describes "
                    "reply-only replayability. `confirmed_timeout_event` explicitly identifies "
                    "the additional failure-event replay path.",
                ]
            )
    lines.extend(["", "## Summary", ""])
    if metadata.get("metrics_comparable") is False:
        lines.extend(
            [
                "Metrics are not comparable with a complete baseline: some cases were not run.",
                "Consult the missing scenario IDs and denominator counts "
                "before interpreting results.",
                "",
            ]
        )
    metric_rows = []
    for name, metric in sorted(metrics.items()):
        if isinstance(metric, Mapping) and "value" in metric:
            metric_rows.append([_label(str(name)), metric["value"]])
        elif not isinstance(metric, Mapping):
            metric_rows.append([_label(str(name)), metric])
    lines.extend(_table(["Metric", "Observed value"], metric_rows) if metric_rows else ["n/a"])
    seen_tables: set[str] = set()
    for name, table in _breakdowns(metrics):
        table_key = name.rsplit(".", 1)[-1] + json.dumps(table, sort_keys=True)
        if table_key not in seen_tables:
            lines.extend(["", f"## {_label(name)}", "", *_breakdown_table(table)])
            seen_tables.add(table_key)
    # Keep all details attributable, including denominators, exclusions and unmeasured reasons.
    for name, metric in sorted(metrics.items()):
        if isinstance(metric, Mapping) and metric.get("details") is not None:
            details = (
                {
                    key: value
                    for key, value in metric["details"].items()
                    if key not in {"per_key", "per_rule"}
                }
                if isinstance(metric["details"], Mapping)
                else metric["details"]
            )
            if details:
                detail_lines = (
                    _table(["Detail", "Recorded value"], list(sorted(details.items())))
                    if isinstance(details, Mapping)
                    else [_cell(details)]
                )
                lines.extend(["", f"### {_label(str(name))} details", "", *detail_lines])
    lines.extend(["", "## Failing or incomplete scenario IDs", ""])
    failures = failing_scenario_ids(report)
    lines.extend(
        [f"- {_cell(identifier)}" for identifier in failures] or ["None reported by this run."]
    )
    lines.extend(["", "## Scenario outcomes", ""])
    rows = []
    replay_columns = any(
        isinstance(result, Mapping) and "replay_method" in result for result in results
    )
    for result in results:
        if not isinstance(result, Mapping):
            continue
        row = [
            result.get("id", result.get("scenario_id")),
            result.get("status"),
            result.get("rules_validation"),
            result.get("error") or result.get("reason") or result.get("error_code"),
        ]
        if replay_columns:
            row.extend([result.get("replay_method"), result.get("failure_event_replayed")])
        rows.append(row)
    headers = ["Scenario", "Execution status", "Rule expectations matched", "Error / reason"]
    if replay_columns:
        headers.extend(["Replay method", "Failure event replayed"])
    lines.extend(_table(headers, rows) if rows else ["n/a"])
    return "\n".join(lines) + "\n"


def write_report(
    report: Mapping[str, Any],
    output_dir: Path,
    *,
    timestamp: datetime | None = None,
) -> ReportPaths:
    """Write paired receipts without overwriting another run; atomically refresh LATEST.md."""
    json_text = (
        json.dumps(dict(report), indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False)
        + "\n"
    )
    markdown = render_markdown(report)
    output_dir.mkdir(parents=True, exist_ok=True)
    when = timestamp or datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    base = when.astimezone(UTC).strftime("%Y%m%d-%H%M%S")
    counter = 0
    while True:
        stem = base if counter == 0 else f"{base}-{counter:02d}"
        json_path, markdown_path = output_dir / f"{stem}.json", output_dir / f"{stem}.md"
        counter += 1
        if markdown_path.exists():
            continue
        try:
            json_file = json_path.open("x", encoding="utf-8")
        except FileExistsError:
            continue
        try:
            markdown_file = markdown_path.open("x", encoding="utf-8")
        except FileExistsError:
            json_file.close()
            json_path.unlink()  # only our just-reserved, still-empty file
            continue
        with json_file, markdown_file:
            json_file.write(json_text)
            markdown_file.write(markdown)
        break
    latest = output_dir / "LATEST.md"
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=output_dir, delete=False
    ) as temporary:
        temporary.write(
            f"Latest report: [{markdown_path.name}]({markdown_path.name}) "
            f"· [JSON]({json_path.name})\n\n{markdown}"
        )
        temporary_path = Path(temporary.name)
    try:
        os.replace(temporary_path, latest)
    finally:
        temporary_path.unlink(missing_ok=True)
    return ReportPaths(json_path, markdown_path, latest)
