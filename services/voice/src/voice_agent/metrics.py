"""Minimal Prometheus text-exposition metrics, dependency-free.

Exposes just enough for alerting on the things that matter operationally: how many calls
are live right now, how many turns have run, and how many turns failed (by reason). Kept
intentionally small — no third-party metrics client — and rendered on demand at /metrics.
"""

from __future__ import annotations

from threading import Lock

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class MetricsRegistry:
    """Process-local counters for voice-turn outcomes. Counters only ever increase, so a
    scrape gap never loses information (the value is cumulative)."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._turns_total = 0
        self._turn_failures_by_outcome: dict[str, int] = {}

    def record_turn(self, outcome: str) -> None:
        clean_outcome = outcome.strip().lower() or "unknown"
        with self._lock:
            self._turns_total += 1
            if clean_outcome != "success":
                self._turn_failures_by_outcome[clean_outcome] = (
                    self._turn_failures_by_outcome.get(clean_outcome, 0) + 1
                )

    def render(self, *, active_media_sessions: int) -> str:
        with self._lock:
            turns_total = self._turns_total
            failures = dict(sorted(self._turn_failures_by_outcome.items()))
        lines = [
            "# HELP voice_agent_active_media_sessions Currently active media calls.",
            "# TYPE voice_agent_active_media_sessions gauge",
            f"voice_agent_active_media_sessions {active_media_sessions}",
            "# HELP voice_agent_turns_total Voice turns processed (terminal outcomes).",
            "# TYPE voice_agent_turns_total counter",
            f"voice_agent_turns_total {turns_total}",
            "# HELP voice_agent_turn_failures_total Failed voice turns by outcome.",
            "# TYPE voice_agent_turn_failures_total counter",
        ]
        for outcome, count in failures.items():
            lines.append(
                f'voice_agent_turn_failures_total{{outcome="{_escape_label_value(outcome)}"}} '
                f"{count}"
            )
        return "\n".join(lines) + "\n"
