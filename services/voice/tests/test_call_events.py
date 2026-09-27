from typing import Any

import pytest

from voice_agent.config.settings import Settings
from voice_agent.storage.call_events import RedisCallEventStore, build_call_event_store


@pytest.mark.anyio
async def test_redis_call_event_store_uses_configured_stream_maxlen() -> None:
    store = RedisCallEventStore(redis_url="redis://redis:6379/0", stream_maxlen=42)
    fake_redis = FakeRedis()
    store._redis = fake_redis

    await store.record({"CallUUID": "call-1", "nested": {"status": "completed"}})

    assert fake_redis.xadd_calls == [
        {
            "stream_name": "plivo:call_events",
            "fields": {"CallUUID": "call-1", "nested": '{"status":"completed"}'},
            "maxlen": 42,
            "approximate": True,
        }
    ]


@pytest.mark.anyio
async def test_redis_call_event_store_reads_recent_events() -> None:
    store = RedisCallEventStore(redis_url="redis://redis:6379/0")
    fake_redis = FakeRedis()
    fake_redis.xrevrange_result = [
        ("event-2", {"event": "voice_turn_latency", "turn_committed_to_llm_first_chunk_ms": "42"}),
        ("event-1", {"event": "media_start"}),
    ]
    store._redis = fake_redis

    events = await store.recent(count=2)

    assert fake_redis.xrevrange_calls == [
        {"stream_name": "plivo:call_events", "count": 2}
    ]
    assert events == [
        {"event": "voice_turn_latency", "turn_committed_to_llm_first_chunk_ms": "42"},
        {"event": "media_start"},
    ]


def test_build_call_event_store_passes_configured_stream_maxlen() -> None:
    store = build_call_event_store(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="redis",
            redis_url="redis://redis:6379/0",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            call_event_stream_maxlen=42,
        )
    )

    assert isinstance(store, RedisCallEventStore)
    assert store._stream_maxlen == 42


class FakeRedis:
    def __init__(self) -> None:
        self.xadd_calls: list[dict[str, Any]] = []
        self.xrevrange_calls: list[dict[str, Any]] = []
        self.xrevrange_result: list[tuple[str, dict[str, str]]] = []

    async def xadd(
        self,
        stream_name: str,
        fields: dict[str, str],
        *,
        maxlen: int,
        approximate: bool,
    ) -> str:
        self.xadd_calls.append(
            {
                "stream_name": stream_name,
                "fields": fields,
                "maxlen": maxlen,
                "approximate": approximate,
            }
        )
        return "event-1"

    async def xrevrange(
        self,
        stream_name: str,
        *,
        count: int,
    ) -> list[tuple[str, dict[str, str]]]:
        self.xrevrange_calls.append({"stream_name": stream_name, "count": count})
        return self.xrevrange_result

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None
