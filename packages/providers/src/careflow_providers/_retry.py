"""Transient-failure retry helpers, ported from the owner's plivo-voice-agent
(`src/voice_agent/providers/_retry.py`).

A request is retried only while it is being established, never after streaming has started (that
could replay half a reply). Retries fire on connection errors, 429 and 5xx, with capped
exponential backoff that honours `Retry-After`.
"""

from __future__ import annotations

import httpx

RETRYABLE_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


def compute_backoff(attempt: int, *, base_seconds: float, max_seconds: float) -> float:
    """Capped exponential backoff: base, 2*base, 4*base, ... clamped to max_seconds."""
    multiplier = 1 << max(attempt - 1, 0)
    return min(base_seconds * multiplier, max_seconds)


def retry_after_seconds(response: httpx.Response, *, max_seconds: float) -> float | None:
    """Parse a delta-seconds `Retry-After` header, clamped to max_seconds. HTTP-date forms are
    treated as absent so a malformed header never stalls a request."""
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        seconds = float(raw.strip())
    except ValueError:
        return None
    if seconds < 0:
        return None
    return min(seconds, max_seconds)
