"""The queue simulation (M1-10): 8 hours at 200 arrivals/hour, run on QueueCore itself.

uv run python -m careflow_api.queue.sim     (writes the report to eval/reports/queue/)
"""

from __future__ import annotations

from datetime import date
from functools import cache

from careflow_api.queue.core import Status
from careflow_api.queue.sim import SimConfig, SimResult, percentile, report, run


@cache
def result() -> SimResult:
    return run(SimConfig(seed=42))


def test_the_load_is_the_one_the_issue_describes() -> None:
    r = result()
    assert r.config.arrivals_per_hour == 200 and r.config.rooms == 66 and r.config.hours == 8
    assert 1500 <= len(r.patients) <= 1700  # Poisson around 1,600
    assert 0.80 <= r.utilisation <= 0.90


def test_a_free_room_always_takes_a_waiting_level_1_or_2_first() -> None:
    r = result()
    urgent_calls = [c for c in r.calls if c[2] <= 2]
    assert urgent_calls, "the run must include urgent tokens that waited for a call"
    assert all(level <= 2 for _, level, lowest in r.calls if lowest <= 2)
    assert r.urgent_left_waiting == 0  # never a free room they fit while they wait


def test_levels_1_and_2_wait_at_most_10_minutes_at_p95() -> None:
    r = result()
    for level in (1, 2):
        waits = r.waits(level)
        assert waits and percentile(waits, 95) <= 10, (level, percentile(waits, 95))


def test_the_queue_does_not_grow_over_the_run() -> None:
    r = result()
    assert r.mean_waiting_last_hour <= 2 * r.mean_waiting_run


def test_every_no_show_had_exactly_one_recall() -> None:
    r = result()
    missed = sum(p.misses for p in r.patients.values())
    assert 0.03 <= missed / len(r.calls) <= 0.07  # about 5% of calls are no-shows
    for token in r.tokens.values():
        if token.status is Status.no_show:
            assert token.recall_count == 1 and r.patients[token.token_id].misses == 2
        else:
            assert r.patients[token.token_id].misses == token.recall_count
    assert all(
        p.seen is None for t, p in r.patients.items() if r.tokens[t].status is Status.no_show
    )


def test_the_report_lists_every_level() -> None:
    text = report(result(), date(2026, 9, 27))
    assert "| 5 |" in text and "Levels 4-5 wait longest" in text
