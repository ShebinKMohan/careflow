"""Per-call agent overrides carried from the outbound request (or inbound answer)
to the media session.

A call traverses three independent HTTP/WS hops inside the agent —
``POST /calls/outbound`` (or, for inbound, ``POST /plivo/answer``), the Plivo answer
callback, and the ``/plivo/media`` WebSocket. The per-project model, system prompt,
and greeting are known at the first hop, but must be applied at the last. This store
bridges that gap: the first hop stashes the overrides under a random ``cfg`` id, that
id rides the answer XML onto the media-WS URL, and the media handler reads them back.

Two backends, mirroring the call-session store: an in-process map (correct for a
single agent process) and a Redis-backed map (so the ``cfg`` id resolves across
workers, since the three hops can land on different workers under ``--workers``).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import redis.asyncio as redis

from voice_agent.config.settings import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PerCallLLMConfig:
    """Per-call agent overrides resolved from a project's agent-config at call launch:
    the LLM model, the system prompt, and the opening line (greeting) the agent speaks."""

    provider: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    greeting: str | None = None
    temperature: float | None = None
    behavior: dict[str, Any] | None = None
    stt: dict[str, Any] | None = None
    tts: dict[str, Any] | None = None

    @property
    def is_empty(self) -> bool:
        return not (
            self.provider
            or self.model
            or self.system_prompt
            or self.greeting
            or self.temperature is not None
            or self.behavior
            or self.stt
            or self.tts
        )


class PerCallConfigStore(Protocol):
    """Maps a random ``cfg`` id to per-call overrides, with a TTL covering the
    outbound -> answer -> media-WS window."""

    async def put(self, key: str, config: PerCallLLMConfig) -> None: ...

    async def get(self, key: str) -> PerCallLLMConfig | None: ...

    async def pop(self, key: str) -> PerCallLLMConfig | None: ...

    async def health_check(self) -> bool: ...

    async def close(self) -> None: ...


class InMemoryPerCallConfigStore:
    """Bounded, TTL-expiring in-process map. Correct for a single agent process."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 900.0,
        max_entries: int = 10_000,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive.")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive.")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock or time.monotonic
        self._entries: dict[str, tuple[float, PerCallLLMConfig]] = {}

    async def put(self, key: str, config: PerCallLLMConfig) -> None:
        clean_key = key.strip()
        if not clean_key:
            raise ValueError("key must not be blank.")
        now = self._clock()
        self._evict_expired(now)
        if len(self._entries) >= self._max_entries and clean_key not in self._entries:
            self._evict_oldest()
        self._entries[clean_key] = (now + self._ttl_seconds, config)

    async def get(self, key: str) -> PerCallLLMConfig | None:
        clean_key = key.strip()
        if not clean_key:
            return None
        entry = self._entries.get(clean_key)
        if entry is None:
            return None
        expires_at, config = entry
        if expires_at < self._clock():
            self._entries.pop(clean_key, None)
            return None
        return config

    async def pop(self, key: str) -> PerCallLLMConfig | None:
        config = await self.get(key)
        self._entries.pop(key.strip(), None)
        return config

    async def health_check(self) -> bool:
        # An in-process map has no external dependency; it is healthy whenever the
        # process is up.
        return True

    async def close(self) -> None:
        self._entries.clear()

    def _evict_expired(self, now: float) -> None:
        expired = [key for key, (expires_at, _) in self._entries.items() if expires_at < now]
        for key in expired:
            self._entries.pop(key, None)

    def _evict_oldest(self) -> None:
        oldest_key = min(self._entries, key=lambda key: self._entries[key][0], default=None)
        if oldest_key is not None:
            self._entries.pop(oldest_key, None)


class RedisPerCallConfigStore:
    """Redis-backed store so the ``cfg`` id resolves across worker processes."""

    def __init__(
        self,
        redis_url: str,
        *,
        ttl_seconds: int = 900,
        key_prefix: str = "vca:percall:",
        socket_timeout_seconds: float = 2.0,
        connect_timeout_seconds: float = 2.0,
        health_check_interval_seconds: float = 30.0,
        max_connections: int = 50,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive.")
        self._redis: Any = redis.from_url(  # type: ignore[no-untyped-call]
            redis_url,
            decode_responses=True,
            socket_timeout=socket_timeout_seconds,
            socket_connect_timeout=connect_timeout_seconds,
            health_check_interval=int(health_check_interval_seconds),
            max_connections=max_connections,
        )
        self._ttl_seconds = ttl_seconds
        self._prefix = key_prefix

    def _key(self, key: str) -> str:
        return self._prefix + key

    async def put(self, key: str, config: PerCallLLMConfig) -> None:
        clean_key = key.strip()
        if not clean_key:
            raise ValueError("key must not be blank.")
        await self._redis.set(self._key(clean_key), _dumps(config), ex=self._ttl_seconds)

    async def get(self, key: str) -> PerCallLLMConfig | None:
        clean_key = key.strip()
        if not clean_key:
            return None
        raw = await self._redis.get(self._key(clean_key))
        return _loads(raw) if raw is not None else None

    async def pop(self, key: str) -> PerCallLLMConfig | None:
        clean_key = key.strip()
        if not clean_key:
            return None
        raw = await self._redis.get(self._key(clean_key))
        await self._redis.delete(self._key(clean_key))
        return _loads(raw) if raw is not None else None

    async def health_check(self) -> bool:
        # Per-call overrides resolve across workers via Redis; if the readiness probe
        # cannot PING it, this replica cannot serve per-call config and is not ready.
        try:
            return bool(await self._redis.ping())
        except Exception:
            logger.debug("per-call config Redis health check failed.", exc_info=True)
            return False

    async def close(self) -> None:
        await self._redis.aclose()


def _dumps(config: PerCallLLMConfig) -> str:
    return json.dumps(
        {
            "provider": config.provider,
            "model": config.model,
            "system_prompt": config.system_prompt,
            "greeting": config.greeting,
            "temperature": config.temperature,
            "behavior": config.behavior,
            "stt": config.stt,
            "tts": config.tts,
        }
    )


def _loads(raw: str) -> PerCallLLMConfig | None:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    behavior = data.get("behavior")
    stt = data.get("stt")
    tts = data.get("tts")
    return PerCallLLMConfig(
        provider=data.get("provider"),
        model=data.get("model"),
        system_prompt=data.get("system_prompt"),
        greeting=data.get("greeting"),
        temperature=data.get("temperature"),
        behavior=behavior if isinstance(behavior, dict) else None,
        stt=stt if isinstance(stt, dict) else None,
        tts=tts if isinstance(tts, dict) else None,
    )


def build_per_call_config_store(settings: Settings) -> PerCallConfigStore:
    """In-memory for a single process; Redis when the session backend is Redis, so the
    ``cfg`` id resolves across workers."""
    if settings.session_backend == "redis" and settings.redis_url:
        return RedisPerCallConfigStore(
            settings.redis_url,
            socket_timeout_seconds=settings.redis_socket_timeout_seconds,
            connect_timeout_seconds=settings.redis_connect_timeout_seconds,
            health_check_interval_seconds=settings.redis_health_check_interval_seconds,
            max_connections=settings.redis_max_connections,
        )
    return InMemoryPerCallConfigStore()
