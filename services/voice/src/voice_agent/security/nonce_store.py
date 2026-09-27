"""Single-use nonce tracking for replay protection.

Used (opt-in) to make stream tokens single-use and to reject replayed Plivo webhook nonces.
``claim`` returns True the first time a nonce is seen and False on any subsequent attempt
within the TTL. The Redis backend uses an atomic SET NX EX so the check is correct across
replicas; the in-memory backend is per-process (fine for single-instance / dev).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol

import redis.asyncio as redis

# Bound the in-memory map so it cannot grow without limit under nonce churn.
_MAX_TRACKED_NONCES = 100_000


class NonceStore(Protocol):
    async def claim(self, nonce: str, *, ttl_seconds: int) -> bool: ...

    async def close(self) -> None: ...


class InMemoryNonceStore:
    def __init__(self, *, time_fn: Callable[[], float] = time.monotonic) -> None:
        self._time_fn = time_fn
        self._expiry_by_nonce: dict[str, float] = {}

    async def claim(self, nonce: str, *, ttl_seconds: int) -> bool:
        now = self._time_fn()
        self._purge_expired(now)
        existing = self._expiry_by_nonce.get(nonce)
        if existing is not None and existing > now:
            return False
        if len(self._expiry_by_nonce) >= _MAX_TRACKED_NONCES:
            self._expiry_by_nonce.clear()
        self._expiry_by_nonce[nonce] = now + max(ttl_seconds, 1)
        return True

    async def close(self) -> None:
        self._expiry_by_nonce.clear()

    def _purge_expired(self, now: float) -> None:
        expired = [key for key, expiry in self._expiry_by_nonce.items() if expiry <= now]
        for key in expired:
            del self._expiry_by_nonce[key]


class RedisNonceStore:
    def __init__(
        self,
        *,
        redis_url: str,
        key_prefix: str = "voice_agent:nonce:",
        socket_timeout_seconds: float = 2.0,
        connect_timeout_seconds: float = 2.0,
        health_check_interval_seconds: float = 30.0,
        max_connections: int = 50,
    ) -> None:
        self._redis: Any = redis.from_url(  # type: ignore[no-untyped-call]
            redis_url,
            decode_responses=True,
            socket_timeout=socket_timeout_seconds,
            socket_connect_timeout=connect_timeout_seconds,
            health_check_interval=int(health_check_interval_seconds),
            max_connections=max_connections,
        )
        self._key_prefix = key_prefix

    async def claim(self, nonce: str, *, ttl_seconds: int) -> bool:
        # SET key 1 NX EX ttl: atomic claim. Returns True only on first set; None if it
        # already exists (a replay).
        result = await self._redis.set(
            self._key_prefix + nonce, "1", nx=True, ex=max(ttl_seconds, 1)
        )
        return bool(result)

    async def close(self) -> None:
        await self._redis.aclose()


def build_nonce_store(
    *,
    session_backend: str,
    redis_url: str | None,
    socket_timeout_seconds: float = 2.0,
    connect_timeout_seconds: float = 2.0,
    health_check_interval_seconds: float = 30.0,
    max_connections: int = 50,
) -> NonceStore:
    if session_backend == "redis" and redis_url:
        return RedisNonceStore(
            redis_url=redis_url,
            socket_timeout_seconds=socket_timeout_seconds,
            connect_timeout_seconds=connect_timeout_seconds,
            health_check_interval_seconds=health_check_interval_seconds,
            max_connections=max_connections,
        )
    return InMemoryNonceStore()
