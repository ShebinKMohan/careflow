"""Per-IP fixed-window throttling as FastAPI dependencies.

Ported from the owner's voice-dashboard control plane (`app/ratelimit.py`): Redis/Valkey-backed
(shared across workers) with an in-process fallback, so a Redis outage degrades to per-worker
limiting instead of failing open or blocking everything.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request, status

from careflow_api.config import get_settings

logger = logging.getLogger(__name__)

_redis_client: Any = None
_redis_init = False
_memory: dict[str, tuple[float, int]] = {}
key_prefix = "cf:rl"  # tests use a unique prefix so buckets never leak between runs


def _get_redis() -> Any:
    global _redis_client, _redis_init
    if not _redis_init:
        _redis_init = True
        try:
            import redis

            _redis_client = redis.Redis.from_url(
                get_settings().redis_url, socket_timeout=0.5, socket_connect_timeout=0.5
            )
        except Exception:  # noqa: BLE001
            logger.warning("Rate-limit Redis client unavailable; using in-process limiter.")
            _redis_client = None
    return _redis_client


def _hit_memory(key: str, limit: int, window_seconds: int) -> bool:
    now = time.monotonic()
    reset_at, count = _memory.get(key, (now + window_seconds, 0))
    if now > reset_at:
        reset_at, count = now + window_seconds, 0
    count += 1
    _memory[key] = (reset_at, count)
    return count <= limit


def _hit(key: str, limit: int, window_seconds: int) -> bool:
    client = _get_redis()
    if client is not None:
        try:
            count = int(client.incr(key))
            if count == 1:
                client.expire(key, window_seconds)
            return count <= limit
        except Exception:  # noqa: BLE001
            logger.warning("Rate-limit Redis error; falling back to in-process limiter.")
    return _hit_memory(key, limit, window_seconds)


def reset_memory() -> None:
    """Clear the in-process buckets (tests)."""
    _memory.clear()


def rate_limit(name: str, *, limit: int, window_seconds: int) -> Callable[[Request], None]:
    """Throttle to `limit` requests per `window_seconds` per client IP; 429 when exceeded."""

    def dependency(request: Request) -> None:
        ip = request.client.host if request.client else "unknown"
        if not _hit(f"{key_prefix}:{name}:{ip}", limit, window_seconds):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many attempts. Please wait a minute and try again.",
            )

    return dependency


login_rate_limit = rate_limit("login", limit=5, window_seconds=60)
device_token_rate_limit = rate_limit("device-token", limit=30, window_seconds=60)
