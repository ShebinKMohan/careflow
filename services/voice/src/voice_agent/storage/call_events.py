from __future__ import annotations

import json
from typing import Any, Protocol

import redis.asyncio as redis

from voice_agent.config.settings import DEFAULT_CALL_EVENT_STREAM_MAXLEN, Settings


class CallEventStore(Protocol):
    async def record(self, event: dict[str, Any]) -> None: ...

    async def recent(self, *, count: int) -> list[dict[str, Any]]: ...

    async def health_check(self) -> bool: ...

    async def close(self) -> None: ...


class InMemoryCallEventStore:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def record(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    async def recent(self, *, count: int) -> list[dict[str, Any]]:
        return list(reversed(self.events[-count:]))

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


class RedisCallEventStore:
    def __init__(
        self,
        *,
        redis_url: str,
        stream_name: str = "plivo:call_events",
        stream_maxlen: int = DEFAULT_CALL_EVENT_STREAM_MAXLEN,
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
        self._stream_name = stream_name
        self._stream_maxlen = stream_maxlen

    async def record(self, event: dict[str, Any]) -> None:
        serialized = {key: _serialize_value(value) for key, value in event.items()}
        await self._redis.xadd(
            self._stream_name,
            serialized,
            maxlen=self._stream_maxlen,
            approximate=True,
        )

    async def recent(self, *, count: int) -> list[dict[str, Any]]:
        rows = await self._redis.xrevrange(self._stream_name, count=count)
        events: list[dict[str, Any]] = []
        for _event_id, fields in rows:
            if isinstance(fields, dict):
                events.append(dict(fields))
        return events

    async def health_check(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except redis.RedisError:
            return False

    async def close(self) -> None:
        await self._redis.aclose()


def build_call_event_store(settings: Settings) -> CallEventStore:
    if settings.session_backend == "memory":
        return InMemoryCallEventStore()
    if not settings.redis_url:
        raise ValueError("redis_url is required for Redis call-event storage.")
    return RedisCallEventStore(
        redis_url=settings.redis_url,
        stream_maxlen=settings.call_event_stream_maxlen,
        socket_timeout_seconds=settings.redis_socket_timeout_seconds,
        connect_timeout_seconds=settings.redis_connect_timeout_seconds,
        health_check_interval_seconds=settings.redis_health_check_interval_seconds,
        max_connections=settings.redis_max_connections,
    )


def _serialize_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(",", ":"), sort_keys=True)
