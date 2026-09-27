"""A small dependency-free sliding-window rate limiter.

Used to cap the billable control endpoints (preview generation, outbound dialing) so a
misbehaving or compromised control client cannot drive unbounded provider/telephony spend.
It is process-local: behind multiple replicas each instance enforces its own window, which
still bounds per-instance load. For cluster-wide limits, front it with a shared store.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from threading import Lock

# Bound how many distinct keys we track so the limiter itself cannot be turned into a
# memory-exhaustion vector by rotating keys.
_MAX_TRACKED_KEYS = 10_000


class SlidingWindowRateLimiter:
    def __init__(
        self,
        *,
        max_requests: int,
        window_seconds: float,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_requests <= 0:
            raise ValueError("max_requests must be positive.")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive.")
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._time_fn = time_fn
        self._lock = Lock()
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        """Return True if a request for `key` is within the window budget (and record it),
        False if it should be rejected."""
        now = self._time_fn()
        cutoff = now - self._window_seconds
        with self._lock:
            bucket = self._hits.get(key)
            if bucket is None:
                if len(self._hits) >= _MAX_TRACKED_KEYS:
                    self._evict_empty(cutoff)
                bucket = self._hits.setdefault(key, deque())
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self._max_requests:
                return False
            bucket.append(now)
            return True

    def _evict_empty(self, cutoff: float) -> None:
        for key in list(self._hits):
            bucket = self._hits[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if not bucket:
                del self._hits[key]
