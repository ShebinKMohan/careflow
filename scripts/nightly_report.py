"""Write eval/reports/e2e_nightly.json from a Playwright run of the journeys (M2-12).

scripts/nightly-e2e.sh runs the suite on this Mac against the live local model, then calls this
with Playwright's JSON report (e2e/test-results/journeys.json) and the model server's /v1/models
answer. The model is the one scripts/with_llm.sh pins, once the server advertises that exact
snapshot (the check scripts/eval_local.py makes); mlx_lm.server lists every cached model.
The report lists each spec's result, duration and provider, with the timings the journeys measured
(alert and feels-worse latency, reported, never gates) and what they found in HAPI. It names the
model as org/model@revision, never a local path.

    uv run python scripts/nightly_report.py e2e/test-results/journeys.json \\
        --models .run/nightly-models.json --started 2026-09-29T02:00:00Z --stack careflow-e2e
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any, Final

REPO: Final = Path(__file__).resolve().parents[1]
REPORT: Final = REPO / "eval" / "reports" / "e2e_nightly.json"
KEPT: Final = (
    "alert latency",
    "feels-worse latency",
    "turn latency",
    "HAPI",
    "briefing",
    "warning",
)
CAVEATS: Final = (
    "Synthetic personas and a simulated vitals device, ID reader and hospital (HAPI FHIR).",
    "The live local model extracts the facts; nothing is replayed, so results can differ from CI.",
    "Latencies are measured on this machine and reported, never gates.",
    "The voice journeys run only with a voice service (VOICE=1); otherwise they are skipped.",
)


@cache
def _eval_local() -> ModuleType:
    """scripts/eval_local.py, loaded once (the scripts folder is not a package)."""
    spec = importlib.util.spec_from_file_location(
        "careflow_eval_local", REPO / "scripts" / "eval_local.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError("cannot load scripts/eval_local.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def served_model(models: Any, environment: Mapping[str, str]) -> str:
    """The pinned model as org/model@revision, once the server advertises its exact snapshot."""
    local = _eval_local()
    model, revision = local.pinned_model(local.WRAPPER.read_text(encoding="utf-8"), environment)
    local.observed_snapshot(models, model=model, revision=revision)  # raises unless advertised
    return f"{model}@{revision}"


def specs(report: dict[str, Any], model: str) -> list[dict[str, Any]]:
    """One row per test, in the report's order."""
    rows: list[dict[str, Any]] = []

    def walk(suite: dict[str, Any]) -> None:
        for spec in suite.get("specs", []):
            for test in spec.get("tests", []):
                results = test.get("results") or [{}]
                last = results[-1]
                notes = {
                    (note["type"], note.get("description", ""))
                    for note in [*test.get("annotations", []), *last.get("annotations", [])]
                    if note.get("type") in KEPT
                }
                rows.append(
                    {
                        "file": spec["file"],
                        "title": spec["title"],
                        "status": test.get("status"),  # expected, unexpected, flaky or skipped
                        "result": last.get("status"),  # passed, failed, timedOut or skipped
                        "duration_ms": last.get("duration"),
                        "retries": len(results) - 1,
                        "provider": {"extraction": {"provider": "local", "model": model}},
                        "notes": [f"{kind}: {text}" for kind, text in sorted(notes)],
                    }
                )
        for child in suite.get("suites", []):
            walk(child)

    for suite in report.get("suites", []):
        walk(suite)
    return rows


def build(
    report: dict[str, Any], *, model: str, started: str, stack: str, commit: str
) -> dict[str, Any]:
    rows = specs(report, model)
    stats = report.get("stats", {})
    totals = {
        "passed": sum(row["status"] == "expected" for row in rows),
        "failed": sum(row["status"] == "unexpected" for row in rows),
        "flaky": sum(row["status"] == "flaky" for row in rows),
        "skipped": sum(row["status"] == "skipped" for row in rows),
    }
    return {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "started_at": started,
        "commit": commit,
        "mode": "live",
        "stack": stack,
        "provider": {"extraction": {"provider": "local", "model": model}},
        "duration_s": round(float(stats.get("duration", 0)) / 1000, 1),
        "passed": totals["failed"] == 0 and totals["flaky"] == 0 and totals["passed"] > 0,
        "totals": totals,
        "specs": rows,
        "caveats": list(CAVEATS),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("results", type=Path, help="Playwright's JSON report")
    parser.add_argument("--models", type=Path, required=True, help="the server's /v1/models answer")
    parser.add_argument("--started", required=True, help="when the run started (UTC, ISO 8601)")
    parser.add_argument("--stack", required=True)
    parser.add_argument("--out", type=Path, default=REPORT)
    args = parser.parse_args(argv)
    git = shutil.which("git")
    if git is None:
        raise SystemExit("nightly_report: git is not installed")
    commit = subprocess.check_output(  # noqa: S603 - fixed read-only arguments, no shell
        [git, "rev-parse", "HEAD"], cwd=REPO, text=True
    ).strip()
    report = build(
        json.loads(args.results.read_text(encoding="utf-8")),
        model=served_model(json.loads(args.models.read_text(encoding="utf-8")), os.environ),
        started=args.started,
        stack=args.stack,
        commit=commit,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    totals = report["totals"]
    shown = args.out.relative_to(REPO) if args.out.is_relative_to(REPO) else args.out
    print(
        f"nightly_report: {totals['passed']} passed, {totals['failed']} failed, "
        f"{totals['flaky']} flaky, {totals['skipped']} skipped -> {shown}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
