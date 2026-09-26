"""Typed provider errors. Bad model output never raises: see `structured()`."""

from __future__ import annotations


class ProviderUnavailable(RuntimeError):
    """The configured provider cannot serve this task (e.g. no API key). Callers fall back to the
    local or replay provider, or mark the work not_run."""

    def __init__(self, task: str, reason: str) -> None:
        super().__init__(f"provider for task {task!r} unavailable: {reason}")
        self.task = task
        self.reason = reason


class LivePathForbidden(RuntimeError):
    """A subscription CLI was used outside an offline job (ADR-004)."""


class CassetteMiss(LookupError):
    """Replay mode found no recorded response for this request."""
