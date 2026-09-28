"""The recorded Arabic extraction run replays exactly in CI, with no model server (M2-03), and the
committed per-language report is the one the replays produce.

`eval/replays/extraction_ar/` holds the raw replies of the 28 September 2026 local Qwen run over the
100 approved Arabic scenarios. Replaying them through the current extractor and rules must
reproduce every case's output, every metric and the historical latency of that run's report; a
code change that alters any of them fails here.
"""

from __future__ import annotations

import json
from functools import cache
from typing import Any

import pytest

from careflow_eval import extraction_report
from careflow_eval.runner import REPO, run_suite

REPLAYS = REPO / "eval" / "replays"
RECORDED = sorted((REPO / "eval" / "reports" / "extraction_ar").glob("2*.json"))
COMBINED = REPO / "eval" / "reports" / "extraction.json"


@cache
def recorded() -> dict[str, Any]:
    assert len(RECORDED) == 1, RECORDED  # the one live Arabic run
    body: dict[str, Any] = json.loads(RECORDED[0].read_text(encoding="utf-8"))
    return body


@pytest.fixture
def no_live_model(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("the recorded Arabic run replays without a live model")

    monkeypatch.setattr("careflow_eval.runner.build_provider", forbidden)
    monkeypatch.setattr("careflow_eval.runner.providers_config", forbidden)


async def test_the_recorded_arabic_run_replays_exactly(no_live_model: None) -> None:
    baseline = recorded()
    assert baseline["metadata"]["mode"] == "record"
    replay = await run_suite("extraction_ar", replay_dir=REPLAYS)
    metadata = replay["metadata"]
    assert metadata["mode"] == "replay" and metadata["replay_exact"] is True
    assert metadata["language"] == "ar"
    assert metadata["dataset_sha256"] == baseline["metadata"]["dataset_sha256"]
    assert metadata["model_receipt"] == baseline["metadata"]["model_receipt"]
    assert metadata["prompt_version"] == "extraction_v2_ar"
    assert replay["status"] == baseline["status"]
    assert replay["metrics"] == baseline["metrics"]
    assert replay["failed_ids"] == baseline["failed_ids"]
    rows = {row["id"]: row for row in baseline["results"]}
    assert [row["id"] for row in replay["results"]] == list(rows)
    for row in replay["results"]:
        original = rows[row["id"]]
        for field in ("prediction", "expected", "spans", "dialect", "extracted_values"):
            assert row[field] == original[field], (row["id"], field)
        assert row["latency_source"] == "recorded_live"


def test_every_returned_arabic_patient_stated_fact_slices_to_its_quote() -> None:
    rows = recorded()["results"]
    stated = sum(row["spans"]["patient_stated"] for row in rows if row["status"] == "completed")
    sliced = sum(row["spans"]["sliced"] for row in rows if row["status"] == "completed")
    assert stated > 0 and sliced == stated  # 100%


def _committed() -> dict[str, Any]:
    body: dict[str, Any] = json.loads(COMBINED.read_text(encoding="utf-8"))
    assert (REPO / body["sources"]["ar"]["report"]).is_file()
    return body


async def test_the_committed_report_is_the_replays_report(no_live_model: None) -> None:
    committed = _committed()
    reports = {
        "en": await run_suite("extraction_en", replay_dir=REPLAYS),
        "ar": await run_suite("extraction_ar", replay_dir=REPLAYS),
    }
    rebuilt = extraction_report.build(reports, agent_review=extraction_report.load_agent_review())
    for key in (
        "publish",
        "missed_alerts",
        "incomplete_cases",
        "by_language",
        "per_field",
        "span_verified_rate",
        "patient_stated_slice_rate",
        "models",
        "caveats",
        "agent_review",
    ):
        assert committed[key] == rebuilt[key], key
    assert committed["publish"] is False
    assert set(committed["by_language"]) == {"en", "ar", "ar_gulf", "ar_levantine"}
