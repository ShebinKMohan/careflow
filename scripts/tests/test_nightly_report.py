"""scripts/nightly_report.py (M2-12): the nightly journeys' report names the model without a local
path, counts every spec and keeps the timings the journeys measured."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "nightly_report.py"
_spec = importlib.util.spec_from_file_location("nightly_report", SCRIPT)
assert _spec is not None and _spec.loader is not None
nightly = importlib.util.module_from_spec(_spec)
sys.modules["nightly_report"] = nightly
_spec.loader.exec_module(nightly)

SNAPSHOT = (
    "/Users/someone/.cache/huggingface/hub/models--mlx-community--Qwen3.5-9B-MLX-4bit"
    "/snapshots/938d8919941c6e7efd3c7150eff7fe9d12afa631"
)


def test_the_model_is_named_by_org_model_and_revision_never_a_path() -> None:
    assert (
        nightly.model_identity(SNAPSHOT)
        == "mlx-community/Qwen3.5-9B-MLX-4bit@938d8919941c6e7efd3c7150eff7fe9d12afa631"
    )
    assert (
        nightly.model_identity("mlx-community/Qwen3.5-9B-MLX-4bit")
        == "mlx-community/Qwen3.5-9B-MLX-4bit"
    )
    for bad in ("/Users/someone/models/qwen", "qwen", "a/b/c"):
        with pytest.raises(ValueError):
            nightly.model_identity(bad)


def result(status: str, duration: int, notes: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {"status": status, "duration": duration, "annotations": notes or []}


PLAYWRIGHT: dict[str, Any] = {
    "stats": {"duration": 158_000.4, "expected": 2, "unexpected": 1, "skipped": 1, "flaky": 0},
    "suites": [
        {
            "specs": [
                {
                    "file": "stroke.spec.ts",
                    "title": "stroke",
                    "tests": [
                        {
                            "status": "expected",
                            "annotations": [],
                            "results": [
                                result(
                                    "passed",
                                    20_000,
                                    [
                                        {
                                            "type": "alert latency",
                                            "description": "FAST_STROKE: 700 ms",
                                        }
                                    ],
                                )
                            ],
                        }
                    ],
                },
            ],
            "suites": [
                {
                    "specs": [
                        {
                            "file": "routine.spec.ts",
                            "title": "routine",
                            "tests": [{"status": "expected", "results": [result("passed", 5_000)]}],
                        },
                        {
                            "file": "sepsis.spec.ts",
                            "title": "sepsis",
                            "tests": [
                                {"status": "unexpected", "results": [result("failed", 9_000)]}
                            ],
                        },
                        {
                            "file": "consent-voice.spec.ts",
                            "title": "voice",
                            "tests": [{"status": "skipped", "results": [result("skipped", 0)]}],
                        },
                    ]
                }
            ],
        }
    ],
}


def test_the_report_counts_every_spec_and_keeps_the_measured_timings() -> None:
    report = nightly.build(
        PLAYWRIGHT,
        served=SNAPSHOT,
        started="2026-09-29T02:00:00Z",
        stack="careflow-e2e",
        commit="abc",
    )
    assert report["totals"] == {"passed": 2, "failed": 1, "flaky": 0, "skipped": 1}
    assert report["passed"] is False  # a failed spec fails the night
    assert report["mode"] == "live" and report["duration_s"] == 158.0
    rows = {row["file"]: row for row in report["specs"]}
    assert set(rows) == {
        "stroke.spec.ts",
        "routine.spec.ts",
        "sepsis.spec.ts",
        "consent-voice.spec.ts",
    }
    assert rows["stroke.spec.ts"]["notes"] == ["alert latency: FAST_STROKE: 700 ms"]
    assert (
        rows["sepsis.spec.ts"]["result"] == "failed"
        and rows["sepsis.spec.ts"]["duration_ms"] == 9_000
    )
    for row in report["specs"]:
        assert row["provider"]["extraction"]["model"].startswith(
            "mlx-community/Qwen3.5-9B-MLX-4bit@"
        )
    assert "/Users/" not in json.dumps(report)  # never a local path


def test_main_writes_the_report(tmp_path: Path) -> None:
    results = tmp_path / "journeys.json"
    results.write_text(json.dumps(PLAYWRIGHT), encoding="utf-8")
    out = tmp_path / "e2e_nightly.json"
    code = nightly.main(
        [
            str(results),
            "--model",
            SNAPSHOT,
            "--started",
            "2026-09-29T02:00:00Z",
            "--stack",
            "careflow-e2e",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["totals"]["passed"] == 2 and len(written["commit"]) == 40
