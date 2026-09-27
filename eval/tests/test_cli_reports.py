"""CLI and report receipt checks. All provider execution here is controlled, never live."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import yaml

from careflow_eval import cli
from careflow_eval.datasets import content_hash
from careflow_eval.models import Scenario
from careflow_eval.reports import failing_scenario_ids, render_markdown, write_report
from careflow_providers.errors import CassetteMiss


@pytest.fixture
def report() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "suite": "extraction_en",
        "status": "completed",
        "metadata": {
            "git_sha": "abcdef123456",
            "dataset_sha256": "data-hash",
            "mode": "replay",
            "provider": "controlled-test",
            "logical_model": "fixture-only",
            "model_receipt": {"id": "test-model", "purpose": "controlled test; not live evidence"},
            "prompt_version": "extraction-v2",
            "prompt_sha256": "prompt-hash",
            "ruleset_version": "rules-test",
            "latency_source": "recorded_live",
        },
        "results": [
            {
                "id": "case-good",
                "status": "completed",
                "quality_passed": True,
                "rules_validation": True,
            },
            {
                "id": "case-wrong-fact",
                "status": "completed",
                "quality_passed": False,
                "rules_validation": True,
            },
        ],
        "failed_ids": ["case-wrong-fact"],
        "metrics": {
            "field_f1": {
                "value": 0.5,
                "details": {
                    "case_count": 2,
                    "per_key": {
                        "chief_complaint": {
                            "tp": 1,
                            "fp": 1,
                            "fn": 1,
                            "precision": 0.5,
                            "recall": 0.5,
                            "f1": 0.5,
                        },
                    },
                },
            },
            "alert_recall": {
                "value": None,
                "details": {
                    "per_rule": {
                        "FAST": {"expected": 0, "detected": 0, "recall": None},
                    }
                },
            },
            "dropped_fact_rate": {"value": 0.0, "details": {"candidate_rejection_rate": None}},
        },
    }


def test_report_preserves_provenance_undefined_metrics_breakdowns_and_quality_failures(report):
    markdown = render_markdown(report)
    assert "| git_sha | abcdef123456 |" in markdown
    assert "| dataset_sha256 | data-hash |" in markdown
    assert "prompt-hash" in markdown and "rules-test" in markdown
    assert "| alert recall | n/a |" in markdown
    assert "| candidate_rejection_rate | n/a |" in markdown
    assert "per key" in markdown and "chief_complaint" in markdown
    assert "per rule" in markdown and "FAST" in markdown
    assert "- case-wrong-fact" in markdown
    assert "automated, not performed by clinicians" in markdown
    assert "not clinical triage accuracy" in markdown
    assert "replays recorded raw model responses" in markdown
    assert "not a clinical pass" in markdown
    assert failing_scenario_ids(report) == ["case-wrong-fact"]


def test_missing_receipt_fields_remain_na_instead_of_invented(report):
    report["metadata"] = {}
    assert "| git_sha | n/a |" in render_markdown(report)
    assert "| model_receipt | n/a |" in render_markdown(report)


def test_incomplete_metrics_are_explicitly_not_comparable(report):
    report["metadata"].update({"metrics_comparable": False, "missing_ids": ["case-offline"]})
    report["results"].append({"id": "case-offline", "status": "not_run", "reason": "no cassette"})
    markdown = render_markdown(report)
    assert "Metrics are not comparable with a complete baseline" in markdown
    assert "- case-offline" in markdown


def test_timeout_error_code_is_visible_even_with_null_legacy_error_fields(report):
    report["results"].append(
        {
            "id": "case-timeout",
            "status": "completed",
            "rules_validation": False,
            "error": None,
            "reason": None,
            "error_code": "timeout",
        }
    )
    assert "| case-timeout | completed | false | timeout |" in render_markdown(report)
    assert "case-timeout" in failing_scenario_ids(report)


def test_confirmed_timeout_replay_is_distinguished_from_recorded_model_replies(report):
    report["metadata"]["failure_event_replay_count"] = 1
    report["results"][0].update(replay_method="recorded_replies", failure_event_replayed=False)
    report["results"].append(
        {
            "id": "case-timeout",
            "status": "completed",
            "rules_validation": False,
            "error_code": "timeout",
            "recording_replayable": False,
            "replay_method": "confirmed_timeout_event",
            "failure_event_replayed": True,
        }
    )
    original = json.dumps(report, sort_keys=True)
    markdown = render_markdown(report)
    assert "canonical `ReplayProvider`" in markdown
    assert "also replays 1 confirmed timeout event(s)" in markdown
    assert "immediately raising `TimeoutError` through the production extractor" in markdown
    assert "Ordered provider calls" in markdown and "output hash" in markdown
    assert "Historical case latency is retained separately from replay wall time" in markdown
    assert "original `recording_replayable: false` remains unchanged" in markdown
    assert "| case-good | completed | true | n/a | recorded_replies | false |" in markdown
    assert (
        "| case-timeout | completed | false | timeout | confirmed_timeout_event | true |"
        in markdown
    )
    assert json.dumps(report, sort_keys=True) == original


def test_reply_only_replay_does_not_claim_timeout_events(report):
    report["metadata"]["failure_event_replay_count"] = 0
    assert "confirmed timeout event" not in render_markdown(report)


def test_rules_replay_does_not_claim_model_responses(report):
    report["suite"] = "rules"
    markdown = render_markdown(report)
    assert "recorded rules outputs without invoking a model" in markdown
    assert "recorded raw model responses" not in markdown


def test_progress_prints_only_time_case_id_and_count(capsys):
    cli._progress("case-routine", 2, 50)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "UTC] 2/50 case-routine" in captured.err
    assert len(captured.err.splitlines()) == 1


def test_timestamp_collisions_preserve_previous_receipts_and_update_latest(tmp_path, report):
    when = datetime(2026, 9, 27, 9, 1, 2, tzinfo=UTC)
    first = write_report(report, tmp_path, timestamp=when)
    original = first.json_path.read_bytes()
    report["status"] = "incomplete"
    second = write_report(report, tmp_path, timestamp=when)
    assert first.json_path.name == "20260927-090102.json"
    assert second.json_path.name == "20260927-090102-01.json"
    assert first.json_path.read_bytes() == original
    assert json.loads(second.json_path.read_text()) == report
    assert second.markdown_path.name in second.latest_path.read_text()
    assert "**Run status:** incomplete" in second.latest_path.read_text()
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_existing_markdown_also_reserves_its_timestamp(tmp_path, report):
    existing = tmp_path / "20260927-090102.md"
    existing.write_text("historical evidence")
    files = write_report(report, tmp_path, timestamp=datetime(2026, 9, 27, 9, 1, 2))
    assert files.json_path.name == "20260927-090102-01.json"
    assert existing.read_text() == "historical evidence"


def test_nonfinite_measurements_are_refused_before_writing(tmp_path, report):
    report["metrics"]["field_f1"]["value"] = float("nan")
    with pytest.raises(ValueError):
        write_report(report, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_report_cells_do_not_break_tables_and_duplicate_breakdowns_are_suppressed(report):
    report["metadata"]["logical_model"] = "fixture|model\nsecond line"
    report["metrics"]["field_recall"] = report["metrics"]["field_f1"]
    markdown = render_markdown(report)
    assert "fixture\\|model<br>second line" in markdown
    assert markdown.count("## field f1.details.per key") == 1
    assert "## field recall.details.per key" not in markdown


def test_cli_lists_real_metric_registry(capsys):
    assert cli.main(["list"]) == 0
    output = capsys.readouterr().out
    assert "rules" in output and "extraction_en" in output
    assert "field_f1" in output and "latency_p95_ms" in output


def test_cli_run_forwards_options_and_reports_quality_failures_without_failing_execution(
    tmp_path,
    report,
    monkeypatch,
    capsys,
):
    run = AsyncMock(return_value=report)
    monkeypatch.setattr(cli, "_run_suite", run)
    receipt = tmp_path / "model.json"
    receipt.write_text('{"id":"test-model"}')
    output = tmp_path / "reports"
    assert (
        cli.main(
            [
                "run",
                "extraction_en",
                "--dataset",
                str(tmp_path / "data"),
                "--config",
                "providers.yaml",
                "--replay",
                "--model-receipt",
                str(receipt),
                "--timeout",
                "3.5",
                "--include-unreviewed",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    run.assert_awaited_once_with(
        "extraction_en",
        dataset_path=tmp_path / "data",
        provider_config=Path("providers.yaml"),
        record_dir=None,
        replay_dir=cli.REPO / "eval/replays",
        model_receipt={"id": "test-model"},
        timeout_seconds=3.5,
        include_unreviewed=True,
        progress=cli._progress,
    )
    text = capsys.readouterr().out
    assert "Scenario issues reported: 1" in text
    assert "field_f1: 0.5" in text
    assert "alert_recall: n/a" in text
    assert "not a clinical pass" in text
    assert json.loads(next((output / "extraction_en").glob("*.json")).read_text()) == report


@pytest.mark.parametrize("mode", ["record", "replay"])
def test_cli_explicit_record_and_replay_directories(tmp_path, report, monkeypatch, mode):
    run = AsyncMock(return_value=report)
    monkeypatch.setattr(cli, "_run_suite", run)
    assert (
        cli.main(
            [
                "run",
                "extraction_en",
                f"--{mode}",
                str(tmp_path / mode),
                "--output",
                str(tmp_path / "reports"),
            ]
        )
        == 0
    )
    assert run.await_args is not None
    assert run.await_args.kwargs[f"{mode}_dir"] == tmp_path / mode


def test_incomplete_execution_returns_nonzero_but_keeps_the_report(
    tmp_path, report, monkeypatch, capsys
):
    report["status"] = "incomplete"
    monkeypatch.setattr(cli, "_run_suite", AsyncMock(return_value=report))
    assert cli.main(["run", "rules", "--output", str(tmp_path)]) == 1
    assert "Run status: incomplete" in capsys.readouterr().out
    assert (tmp_path / "rules/LATEST.md").exists()


def test_default_reports_are_separated_by_suite(tmp_path, report, monkeypatch):
    monkeypatch.setattr(cli, "DEFAULT_REPORTS", tmp_path)

    async def run(suite: str, **_kwargs: Any) -> dict[str, Any]:
        return {**report, "suite": suite}

    monkeypatch.setattr(cli, "_run_suite", run)
    assert cli.main(["run", "rules"]) == 0
    first_latest = (tmp_path / "rules/LATEST.md").read_bytes()
    assert cli.main(["run", "extraction_en"]) == 0
    assert (tmp_path / "rules/LATEST.md").read_bytes() == first_latest
    assert (tmp_path / "extraction_en/LATEST.md").exists()
    assert not (tmp_path / "LATEST.md").exists()


@pytest.mark.parametrize(
    "error", [CassetteMiss("missing cassette"), ValueError("model receipt mismatch")]
)
def test_model_or_cassette_failures_are_nonzero_and_never_write_success(
    tmp_path, monkeypatch, capsys, error
):
    monkeypatch.setattr(cli, "_run_suite", AsyncMock(side_effect=error))
    assert cli.main(["run", "extraction_en", "--output", str(tmp_path)]) == 1
    assert str(error) in capsys.readouterr().err
    assert not (tmp_path / "LATEST.md").exists()


@pytest.mark.parametrize("receipt", ["[]", "not JSON"])
def test_invalid_model_receipt_never_starts_a_run(tmp_path, monkeypatch, capsys, receipt):
    path = tmp_path / "receipt.json"
    path.write_text(receipt)
    run = AsyncMock()
    monkeypatch.setattr(cli, "_run_suite", run)
    assert cli.main(["run", "extraction_en", "--model-receipt", str(path)]) == 1
    run.assert_not_awaited()
    assert "careflow-eval:" in capsys.readouterr().err


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["run", "unknown"],
        ["run", "rules", "--timeout", "nan"],
        ["run", "rules", "--timeout", "0"],
        ["review"],
        ["review", "--judge", "cli"],
        ["review", "--judge", "cli", "--reviewer-model", " "],
        ["review", "--model-receipt", "receipt.json", "--reviewer-model", "claude-test"],
        [
            "review",
            "--judge",
            "cli",
            "--reviewer-model",
            "claude-test",
            "--model-receipt",
            "receipt.json",
        ],
        ["run", "rules", "--record", "out", "--replay"],
    ],
)
def test_parse_errors_use_standard_exit_two(args):
    with pytest.raises(SystemExit) as caught:
        cli.main(args)
    assert caught.value.code == 2


def _draft(tmp_path: Path, reviewed: bool = False) -> Path:
    body = {
        "id": "cli-routine",
        "synthetic": True,
        "category": "routine",
        "language": "en",
        "description": "Synthetic adult ankle complaint.",
        "patient": {"age_years": 30, "sex": "male"},
        "turns": [{"question_key": "chief_complaint", "patient_text": "I twisted my ankle today."}],
        "expected": {
            "facts": {"chief_complaint": "I twisted my ankle today."},
            "alerts": [],
            "level": 5,
            "level_rationale": "Synthetic test label, not a clinical assessment.",
        },
        # Controlled metadata only: this fixture never invokes either model.
        "generated_by": {"tool": "test", "model": "GPT-6-test-fixture", "date": "2026-09-27"},
    }
    scenario = Scenario.model_validate(body)
    if reviewed:
        body.update(
            {
                "reviewed_by": "agent:test/Qwen3.5-test-fixture",
                "reviewed_at": "2026-09-27T09:00:00Z",
                "review_method": "automated",
                "reviewed_content_sha256": content_hash(scenario),
            }
        )
    path = tmp_path / "cli-routine.yaml"
    path.write_text(yaml.safe_dump(body))
    return path


def test_validate_refuses_unreviewed_dataset_unless_explicit(tmp_path, capsys):
    dataset = _draft(tmp_path)
    assert cli.main(["validate", "intake_en", "--dataset", str(dataset)]) == 1
    assert "reviewed_by" in capsys.readouterr().err
    assert (
        cli.main(["validate", "intake_en", "--dataset", str(dataset), "--include-unreviewed"]) == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["count"] == 1
    assert len(result["dataset_sha256"]) == 64


def test_validate_accepts_content_bound_controlled_review(tmp_path, capsys):
    dataset = _draft(tmp_path, reviewed=True)
    assert cli.main(["validate", "intake_en", "--dataset", str(dataset)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "completed"


def test_generate_dispatches_offline_workflow_without_inventing_review(
    tmp_path, monkeypatch, capsys
):
    generate = AsyncMock(
        return_value={
            "status": "generated",
            "count": 2,
            "written": [],
            "validation_failures": {},
            "review_required": True,
        }
    )
    monkeypatch.setattr(cli, "_generate", generate)
    assert (
        cli.main(
            [
                "generate",
                "--suite",
                "intake_en",
                "--n",
                "2",
                "--dataset",
                str(tmp_path),
                "--writer-model",
                "explicit-model",
            ]
        )
        == 0
    )
    generate.assert_awaited_once_with(
        suite="intake_en",
        n=2,
        dataset_path=tmp_path,
        config_path=None,
        writer_model="explicit-model",
    )
    assert json.loads(capsys.readouterr().out)["review_required"] is True


def test_partial_review_rejection_is_not_success(tmp_path, monkeypatch, capsys):
    receipt = tmp_path / "receipt.json"
    receipt.write_text('{"id":"independent-model"}')
    review = AsyncMock(
        return_value={
            "status": "reviewed",
            "accepted": ["case-a"],
            "rejected": {"case-b": ["unsafe gold"]},
        }
    )
    monkeypatch.setattr(cli, "_review", review)
    assert (
        cli.main(
            ["review", "--model-receipt", str(receipt), "--batch-size", "2", "--timeout", "40"]
        )
        == 1
    )
    review.assert_awaited_once_with(
        dataset_path=None,
        config_path=None,
        model_receipt={"id": "independent-model"},
        judge="local",
        reviewer_model=None,
        batch_size=2,
        timeout_seconds=40.0,
    )
    assert "case-b" in capsys.readouterr().out


def test_cli_review_dispatches_explicit_identity_without_inventing_a_receipt(monkeypatch, capsys):
    review = AsyncMock(return_value={"status": "reviewed", "accepted": 1, "rejected": 0})
    monkeypatch.setattr(cli, "_review", review)
    assert cli.main(["review", "--judge", "cli", "--reviewer-model", "claude-test-fixture"]) == 0
    review.assert_awaited_once_with(
        dataset_path=None,
        config_path=None,
        model_receipt=None,
        judge="cli",
        reviewer_model="claude-test-fixture",
        batch_size=5,
        timeout_seconds=120.0,
    )
    assert json.loads(capsys.readouterr().out)["accepted"] == 1
