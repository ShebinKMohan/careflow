"""A discrete-event simulation of one department's queue, run on `QueueCore` itself (M1-10).

Standard library only: a `heapq` event loop and `random.Random(seed)`. Arrivals are Poisson;
the level mix and the exponential service times follow the issue; a share of calls are no-shows,
who miss the call, are re-queued once by the grace rule and may miss again. Patients who arrive
within the horizon are followed until they are seen, so their waits are complete.

    uv run python -m careflow_api.queue.sim            (writes eval/reports/queue/sim_<date>.md)
"""

from __future__ import annotations

import argparse
import heapq
import math
import random
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Final

from careflow_api.config import REPO_ROOT
from careflow_api.queue.core import QueueCore, Status, Token
from careflow_api.queue.rooms import Department, Room

REPORTS: Final = REPO_ROOT / "eval" / "reports" / "queue"
START: Final = datetime(2026, 1, 1, tzinfo=UTC)
URGENT: Final = frozenset({1, 2})


@dataclass(frozen=True)
class SimConfig:
    hours: float = 8.0
    arrivals_per_hour: float = 200.0
    level_mix: Mapping[int, float] = field(
        default_factory=lambda: {1: 0.02, 2: 0.13, 3: 0.40, 4: 0.35, 5: 0.10}
    )
    service_minutes: Mapping[int, float] = field(
        default_factory=lambda: {1: 30.0, 2: 25.0, 3: 20.0, 4: 12.0, 5: 8.0}
    )
    rooms: int = 66
    #: The first rooms (in calling order) take only levels 1-2, like resus bays.
    resus_rooms: int = 2
    no_show_share: float = 0.05
    grace_seconds: int = 120
    seed: int = 42


@dataclass
class Patient:
    level: int
    arrived: float
    seen: float | None = None
    misses: int = 0


@dataclass
class SimResult:
    config: SimConfig
    patients: dict[str, Patient]
    tokens: dict[str, Token]
    #: One row per call: (seconds, called level, lowest waiting level just before the call).
    calls: list[tuple[float, int, int]]
    #: Times an urgent token waited while a room that accepts it was free, after the rooms filled.
    urgent_left_waiting: int
    service_seconds_in_horizon: float
    waiting_area_run: float
    waiting_area_last_hour: float
    max_waiting: int

    @property
    def horizon(self) -> float:
        return self.config.hours * 3600

    def waits(self, level: int) -> list[float]:
        """Minutes from arrival to being seen, for every patient seen (not no-shows)."""
        return sorted(
            (p.seen - p.arrived) / 60
            for p in self.patients.values()
            if p.level == level and p.seen is not None
        )

    @property
    def utilisation(self) -> float:
        return self.service_seconds_in_horizon / (self.config.rooms * self.horizon)

    @property
    def mean_waiting_run(self) -> float:
        return self.waiting_area_run / self.horizon

    @property
    def mean_waiting_last_hour(self) -> float:
        return self.waiting_area_last_hour / 3600

    @property
    def no_shows(self) -> list[Token]:
        return [t for t in self.tokens.values() if t.status is Status.no_show]


def percentile(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile; NaN for no values."""
    if not values:
        return math.nan
    return sorted(values)[max(math.ceil(p / 100 * len(values)) - 1, 0)]


def department(config: SimConfig) -> Department:
    rooms = tuple(
        Room(
            id=f"room-{i + 1}",
            name=f"Room {i + 1}",
            accepts_levels=frozenset(URGENT if i < config.resus_rooms else range(1, 6)),
        )
        for i in range(config.rooms)
    )
    return Department(id="sim", name="Simulated department", prefix="S", rooms=rooms)


def run(config: SimConfig | None = None) -> SimResult:
    config = config or SimConfig()
    rng = random.Random(config.seed)  # noqa: S311 - a seeded simulation, not security
    now = [0.0]
    core = QueueCore(
        department(config),
        grace_seconds=config.grace_seconds,
        clock=lambda: START + timedelta(seconds=now[0]),
    )
    horizon = config.hours * 3600
    levels, weights = zip(*sorted(config.level_mix.items()), strict=True)
    events: list[tuple[float, int, str, str]] = []
    counter = [0]

    def schedule(at: float, kind: str, token_id: str = "") -> None:
        counter[0] += 1
        heapq.heappush(events, (at, counter[0], kind, token_id))

    patients: dict[str, Patient] = {}
    calls: list[tuple[float, int, int]] = []
    urgent_left_waiting = 0
    service_in_horizon = 0.0
    area_run = area_last = 0.0
    max_waiting = 0
    last_t, last_count = 0.0, 0

    def account(t: float) -> None:
        """Time-weighted waiting count over [0, horizon] and over its last hour."""
        nonlocal area_run, area_last, last_t
        start, end = min(last_t, horizon), min(t, horizon)
        area_run += last_count * (end - start)
        area_last += last_count * max(0.0, end - max(start, horizon - 3600))
        last_t = t

    def fill_rooms(t: float) -> None:
        nonlocal urgent_left_waiting, service_in_horizon
        while True:
            waiting = core.waiting()
            lowest = min((w.level for w in waiting), default=6)
            token = core.call_next_free()
            if token is None:
                break
            calls.append((t, token.level, lowest))
            patient = patients[token.token_id]
            if rng.random() < config.no_show_share:
                patient.misses += 1
                schedule(t + config.grace_seconds, "grace", token.token_id)
                continue
            core.mark_arrived(token.token_id)
            patient.seen = t
            service = rng.expovariate(1 / (config.service_minutes[token.level] * 60))
            service_in_horizon += max(0.0, min(t + service, horizon) - min(t, horizon))
            schedule(t + service, "done", token.token_id)
        free_urgent_room = any(URGENT & room.accepts_levels for room in core.free_rooms())
        if free_urgent_room and any(w.level in URGENT for w in core.waiting()):
            urgent_left_waiting += 1

    schedule(rng.expovariate(config.arrivals_per_hour / 3600), "arrival")
    while events:
        t, _, kind, token_id = heapq.heappop(events)
        account(t)
        now[0] = t
        if kind == "arrival":
            token_id = f"t{len(patients) + 1:05d}"
            level = rng.choices(levels, weights)[0]
            moment = core.clock()
            code = f"S-{len(patients) % 999 + 1:03d}"
            core.add(Token(token_id, token_id, code, level, issued_at=moment, enqueued_at=moment))
            patients[token_id] = Patient(level=level, arrived=t)
            following = t + rng.expovariate(config.arrivals_per_hour / 3600)
            if following < horizon:
                schedule(following, "arrival")
        elif kind == "grace":
            core.expire_grace()
        else:
            core.close(token_id)
        fill_rooms(t)
        last_count = len(core.waiting())
        if t <= horizon:
            max_waiting = max(max_waiting, last_count)
    account(max(last_t, horizon))
    return SimResult(
        config=config,
        patients=patients,
        tokens=core.tokens,
        calls=calls,
        urgent_left_waiting=urgent_left_waiting,
        service_seconds_in_horizon=service_in_horizon,
        waiting_area_run=area_run,
        waiting_area_last_hour=area_last,
        max_waiting=max_waiting,
    )


def report(result: SimResult, day: date) -> str:
    c = result.config
    lines = [
        f"# Queue simulation, {day.isoformat()} (M1-10)",
        "",
        "Research prototype. Synthetic data only. Not for clinical use.",
        "",
        "`uv run python -m careflow_api.queue.sim` runs `QueueCore` itself in a seeded `heapq`",
        "event loop. The acceptance checks run in `services/api/tests/sim/test_queue_sim.py`.",
        "",
        "## Setup",
        "",
        f"- {c.hours:g} hours of Poisson arrivals at {c.arrivals_per_hour:g}/hour, seed {c.seed}.",
        "- Level mix: " + ", ".join(f"{k}: {v:.0%}" for k, v in sorted(c.level_mix.items())) + ".",
        "- Exponential service, mean minutes by level: "
        + ", ".join(f"{k}: {v:g}" for k, v in sorted(c.service_minutes.items()))
        + ".",
        f"- {c.rooms} rooms; the first {c.resus_rooms} take only levels 1-2 (resus).",
        f"- {c.no_show_share:.0%} of calls are no-shows. The grace window is {c.grace_seconds} s;"
        " a first miss re-queues the token once, a second miss makes it a no-show.",
        "- Patients who arrived within the horizon are followed until they are seen.",
        "",
        "## Waits, arrival to being seen (minutes)",
        "",
        "| Level | Patients seen | p50 | p95 | Max |",
        "|---|---|---|---|---|",
    ]
    for level in sorted(c.level_mix):
        waits = result.waits(level)
        lines.append(
            f"| {level} | {len(waits)} | {percentile(waits, 50):.1f} | "
            f"{percentile(waits, 95):.1f} | {max(waits, default=math.nan):.1f} |"
        )
    recalls = sum(1 for t in result.tokens.values() if t.recall_count == 1)
    missed_calls = sum(p.misses for p in result.patients.values())
    lines += [
        "",
        "## Load and fairness",
        "",
        f"- Room utilisation (service time within the horizon): {result.utilisation:.3f}.",
        f"- Calls: {len(result.calls)}; no-show calls: {missed_calls}; tokens re-queued: {recalls};"
        f" no-shows: {len(result.no_shows)}.",
        f"- Waiting count: mean {result.mean_waiting_run:.2f} over the run, "
        f"{result.mean_waiting_last_hour:.2f} in the last hour; maximum {result.max_waiting}.",
        f"- Level 1-2 tokens left waiting while a room that takes them was free: "
        f"{result.urgent_left_waiting}.",
        "- Levels 4-5 wait longest under strict priority (their p95 is above). Aging, which would "
        "bound it, is a later decision; it is reported here, not hidden.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--date", type=date.fromisoformat, default=None, help="report date")
    args = parser.parse_args(argv)
    result = run(SimConfig(seed=args.seed))
    day = args.date or datetime.now().astimezone().date()
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"sim_{day:%Y%m%d}.md"
    path.write_text(report(result, day), encoding="utf-8")
    print(path.relative_to(REPO_ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
