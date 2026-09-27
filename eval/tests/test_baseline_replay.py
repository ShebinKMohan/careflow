"""The committed live baselines replay exactly in CI, with no model server.

`eval/replays/` holds the raw replies (and one confirmed timeout event) recorded by the
27 September 2026 local Qwen run. Replaying them through the current extractor and rules must
reproduce every case's output, every quality metric and the historical latency percentiles of
the retained baseline reports; a code change that alters any of them fails here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from careflow_eval import cli
from careflow_eval.runner import REPO, run_suite

REPLAYS = REPO / "eval" / "replays"
EXTRACTION_BASELINE = REPO / "eval" / "reports" / "extraction_en" / "20260927-095128.json"
RULES_BASELINE = REPO / "eval" / "reports" / "rules" / "20260927-095225.json"
DATASET_SHA256 = "b8e3c26807e65a4d634c29bbb4fa783e78f5880eb22399c1bcd019e49d4f0bc9"
RECORDED_AT_COMMIT = "989981b5c265a70ac2fb466459694ca9b6dad968"
TIMED_OUT_CASE = "child-parent-unsure-waking"
CASE_FIELDS = (
    "category",
    "status",
    "expected",
    "rules_validation",
    "error_code",
    "output_sha256",
    "field_f1",
    "quality_passed",
    "recording_replayable",
)


@pytest.fixture
def no_live_model(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("the fixed baseline replays without constructing a live model")

    monkeypatch.setattr("careflow_eval.runner.build_provider", forbidden)
    monkeypatch.setattr("careflow_eval.runner.providers_config", forbidden)


def _baseline(path: Path) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return body


def _assert_matches_extraction_baseline(replay: dict[str, Any]) -> None:
    baseline = _baseline(EXTRACTION_BASELINE)
    assert baseline["metadata"]["mode"] == "record"
    assert replay["status"] == "completed"
    metadata = replay["metadata"]
    assert metadata["mode"] == "replay"
    assert metadata["case_count"] == metadata["completed_count"] == 50
    assert metadata["replay_exact"] is True
    assert metadata["metrics_comparable"] is True
    assert metadata["dataset_sha256"] == baseline["metadata"]["dataset_sha256"] == DATASET_SHA256
    assert metadata["logical_model"] == baseline["metadata"]["logical_model"]
    assert metadata["model_receipt"] == baseline["metadata"]["model_receipt"]
    assert metadata["recording_provenance"] == baseline["metadata"]["recording_provenance"]
    assert metadata["recording_provenance"]["git_sha"] == RECORDED_AT_COMMIT
    assert metadata["latency_source"] == ["recorded_live"]

    # Every metric, including its details and the historical p50/p95, exactly as recorded.
    assert replay["metrics"] == baseline["metrics"]
    assert replay["failed_ids"] == baseline["failed_ids"]
    recorded = {row["id"]: row for row in baseline["results"]}
    assert [row["id"] for row in replay["results"]] == list(recorded)
    for row in replay["results"]:
        original = recorded[row["id"]]
        # facts, alerts, incomplete/dropped counts and the historical per-case latency
        assert row["prediction"] == original["prediction"], row["id"]
        for field in CASE_FIELDS:
            assert row[field] == original[field], (row["id"], field)
        assert row["latency_source"] == "recorded_live"

    # The one observed live timeout is reproduced as that event, never as a model reply.
    events = [row["id"] for row in replay["results"] if row["failure_event_replayed"]]
    assert events == [TIMED_OUT_CASE]
    assert metadata["failure_event_replay_count"] == 1
    methods = {row["id"]: row["replay_method"] for row in replay["results"]}
    assert methods.pop(TIMED_OUT_CASE) == "confirmed_timeout_event"
    assert set(methods.values()) == {"recorded_replies"}


async def test_fixed_extraction_baseline_replays_exactly(no_live_model: None) -> None:
    replay = await run_suite("extraction_en", replay_dir=REPLAYS)
    _assert_matches_extraction_baseline(replay)
    metrics = {name: body["value"] for name, body in replay["metrics"].items()}
    # The observed numbers, restated so a regenerated baseline cannot pass silently.
    assert metrics["field_f1"] == 0.7417475728155339
    assert metrics["field_precision"] == 0.8025210084033614
    assert metrics["field_recall"] == 0.6895306859205776
    assert metrics["alert_recall"] == 0.96
    assert metrics["false_alert_rate"] == 0.0
    assert metrics["extraction_incomplete_rate"] == 0.02
    assert metrics["latency_p50_ms"] == 9113.418707973324
    assert metrics["latency_p95_ms"] == 12284.610874950886
    timed_out = next(row for row in replay["results"] if row["id"] == TIMED_OUT_CASE)
    assert timed_out["error_code"] == "timeout"
    assert timed_out["prediction"]["extraction_incomplete"] is True
    assert "NG143_RED_FLAG" in timed_out["expected"]["alerts"]
    assert "NG143_RED_FLAG" not in timed_out["prediction"]["alerts"]  # the retained miss


def test_bare_cli_replay_reproduces_the_baseline(no_live_model: None, tmp_path: Path) -> None:
    assert cli.main(["run", "extraction_en", "--replay", "--output", str(tmp_path)]) == 0
    reports = sorted((tmp_path / "extraction_en").glob("2*.json"))
    assert len(reports) == 1
    _assert_matches_extraction_baseline(_baseline(reports[0]))
    assert (tmp_path / "extraction_en" / "LATEST.md").is_file()


async def test_fixed_rules_baseline_replays_exactly(no_live_model: None) -> None:
    baseline = _baseline(RULES_BASELINE)
    replay = await run_suite("rules", replay_dir=REPLAYS)
    assert replay["status"] == "completed"
    assert replay["metadata"]["case_count"] == baseline["metadata"]["case_count"] == 56
    assert replay["metadata"]["replay_exact"] is True
    assert replay["metrics"] == baseline["metrics"]
    assert replay["failed_ids"] == baseline["failed_ids"] == []
    assert replay["metrics"]["alert_recall"]["value"] == 1.0
