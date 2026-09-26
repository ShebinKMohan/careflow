"""Latency: the largest scenario evaluates in under 50 ms, every time (1,000 runs after warm-up)."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import yaml

from careflow_rules import evaluate
from careflow_rules.testing import facts_from_entries

SCENARIOS_DIR: Final = Path(__file__).resolve().parents[3] / "eval" / "scenarios" / "rules"
BUDGET_NS: Final = 50_000_000
NOW: Final = datetime(2026, 9, 26, 9, 5, tzinfo=UTC)
ARRIVED: Final = datetime(2026, 9, 26, 9, 0, tzinfo=UTC)


def largest_scenario() -> dict[str, Any]:
    scenarios = [
        yaml.safe_load(p.read_text(encoding="utf-8")) for p in SCENARIOS_DIR.glob("*.yaml")
    ]
    return max(scenarios, key=lambda s: len(s["facts"]))


def test_max_evaluation_time_is_under_50_ms() -> None:
    scenario = largest_scenario()
    facts = facts_from_entries([{k: str(v) for k, v in e.items()} for e in scenario["facts"]])
    for _ in range(50):
        evaluate(facts, NOW, arrived_at=ARRIVED)
    timings: list[int] = []
    for _ in range(1_000):
        start = time.perf_counter_ns()
        evaluate(facts, NOW, arrived_at=ARRIVED)
        timings.append(time.perf_counter_ns() - start)
    timings.sort()
    print(
        f"benchmark {scenario['id']} ({len(scenario['facts'])} facts): "
        f"p50 {timings[500] / 1e6:.3f} ms, p99 {timings[989] / 1e6:.3f} ms, "
        f"max {timings[-1] / 1e6:.3f} ms"
    )
    assert timings[-1] < BUDGET_NS
