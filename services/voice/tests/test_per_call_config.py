"""Per-call LLM override path: a project's model + system prompt flow from
/calls/outbound through the Plivo answer round-trip to the media session."""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from voice_agent.app import (
    OutboundCallRequest,
    _build_answer_media_websocket_url,
    create_app,
)
from voice_agent.config.settings import Settings
from voice_agent.control_plane import ControlPlaneClient
from voice_agent.per_call_config import (
    InMemoryPerCallConfigStore,
    PerCallLLMConfig,
    RedisPerCallConfigStore,
    build_per_call_config_store,
)
from voice_agent.providers.deepgram import DeepgramStreamingSpeechToText
from voice_agent.providers.factory import build_media_session_runner
from voice_agent.providers.orchestrator import OrchestratorLLMClient
from voice_agent.providers.sarvam import SarvamTextToSpeech
from voice_agent.storage.call_events import InMemoryCallEventStore
from voice_agent.storage.call_sessions import InMemoryCallSessionStore
from voice_agent.telephony.plivo_client import PlivoCallCreateResult

CONTROL_API_KEY = "control-secret-0123456789abcdef012345"


def _local_settings(
    *, control_api_key: str | None = None, plivo_from_number: str | None = None
) -> Settings:
    return Settings(
        environment="test",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="local",
        tts_provider="local",
        control_api_key=control_api_key,
        plivo_from_number=plivo_from_number,
    )


def _orchestrator_settings() -> Settings:
    return Settings(
        environment="test",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="orchestrator",
        orchestrator_url="https://orchestrator.example.com/respond",
        orchestrator_model="claude-haiku-4-5",
        orchestrator_system_prompt="env default prompt",
        tts_provider="local",
    )


class _CapturingTransport:
    async def send_json(self, message: dict[str, Any]) -> None:
        del message


class _Clock:
    def __init__(self, now: float) -> None:
        self._now = now

    def __call__(self) -> float:
        return self._now

    def set(self, now: float) -> None:
        self._now = now


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key: str, value: str, *, ex: int | None = None) -> bool:
        self.values[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def delete(self, key: str) -> int:
        return 1 if self.values.pop(key, None) is not None else 0

    async def aclose(self) -> None:
        return None


def _test_settings(*, session_backend: str) -> Settings:
    return Settings(
        environment="test",
        public_base_url="https://voice.example.com",
        session_backend=session_backend,
        redis_url="redis://redis:6379/0",
        stt_provider="local",
        llm_provider="local",
        tts_provider="local",
    )


# --- PerCallConfigStore: in-memory -----------------------------------------
@pytest.mark.anyio
async def test_store_put_get_roundtrip() -> None:
    store = InMemoryPerCallConfigStore()
    cfg = PerCallLLMConfig(model="claude-opus-4-8", system_prompt="You are Acme.")
    await store.put("k1", cfg)
    assert await store.get("k1") == cfg


@pytest.mark.anyio
async def test_store_get_missing_or_blank_returns_none() -> None:
    store = InMemoryPerCallConfigStore()
    assert await store.get("nope") is None
    assert await store.get("") is None


@pytest.mark.anyio
async def test_store_pop_removes_entry() -> None:
    store = InMemoryPerCallConfigStore()
    await store.put("k", PerCallLLMConfig(model="m"))
    assert await store.pop("k") == PerCallLLMConfig(model="m")
    assert await store.get("k") is None


@pytest.mark.anyio
async def test_inmemory_store_health_check_is_true() -> None:
    # The in-process store has no external dependency, so /readyz treats it as healthy.
    assert await InMemoryPerCallConfigStore().health_check() is True


def test_redis_store_applies_socket_timeouts() -> None:
    # A hung/half-open Redis must raise promptly, not stall the realtime media path; the
    # store passes explicit socket + connect timeouts to the client connection pool.
    store = RedisPerCallConfigStore(
        "redis://localhost:6379/0",
        socket_timeout_seconds=2.5,
        connect_timeout_seconds=1.5,
        health_check_interval_seconds=15.0,
    )
    kwargs = store._redis.connection_pool.connection_kwargs
    assert kwargs["socket_timeout"] == 2.5
    assert kwargs["socket_connect_timeout"] == 1.5
    assert kwargs["health_check_interval"] == 15


def test_build_per_call_config_store_threads_redis_timeouts() -> None:
    settings = Settings(
        environment="test",
        public_base_url="https://voice.example.com",
        session_backend="redis",
        redis_url="redis://localhost:6379/0",
        redis_socket_timeout_seconds=3.0,
        redis_connect_timeout_seconds=1.25,
        stt_provider="local",
        llm_provider="local",
        tts_provider="local",
    )
    store = build_per_call_config_store(settings)
    assert isinstance(store, RedisPerCallConfigStore)
    kwargs = store._redis.connection_pool.connection_kwargs
    assert kwargs["socket_timeout"] == 3.0
    assert kwargs["socket_connect_timeout"] == 1.25


@pytest.mark.anyio
async def test_store_ttl_expires_entries() -> None:
    clock = _Clock(100.0)
    store = InMemoryPerCallConfigStore(ttl_seconds=10.0, clock=clock)
    await store.put("k", PerCallLLMConfig(model="m"))
    clock.set(109.0)
    assert await store.get("k") is not None
    clock.set(111.0)
    assert await store.get("k") is None


@pytest.mark.anyio
async def test_store_evicts_oldest_at_capacity() -> None:
    clock = _Clock(1.0)
    store = InMemoryPerCallConfigStore(max_entries=2, clock=clock)
    await store.put("a", PerCallLLMConfig(model="a"))
    clock.set(2.0)
    await store.put("b", PerCallLLMConfig(model="b"))
    clock.set(3.0)
    await store.put("c", PerCallLLMConfig(model="c"))
    assert await store.get("a") is None
    assert await store.get("b") is not None
    assert await store.get("c") is not None


@pytest.mark.anyio
async def test_store_rejects_blank_key() -> None:
    with pytest.raises(ValueError, match="key must not be blank"):
        await InMemoryPerCallConfigStore().put("  ", PerCallLLMConfig(model="m"))


def test_per_call_config_is_empty() -> None:
    assert PerCallLLMConfig().is_empty
    assert not PerCallLLMConfig(provider="openai").is_empty
    assert not PerCallLLMConfig(model="m").is_empty
    assert not PerCallLLMConfig(system_prompt="p").is_empty
    assert not PerCallLLMConfig(greeting="hello").is_empty


# --- PerCallConfigStore: Redis + factory -----------------------------------
@pytest.mark.anyio
async def test_redis_store_roundtrip_and_pop() -> None:
    store = RedisPerCallConfigStore("redis://redis:6379/0", ttl_seconds=42)
    store._redis = _FakeRedis()
    cfg = PerCallLLMConfig(
        provider="anthropic", model="claude-opus-4-8", system_prompt="P", greeting="Hi."
    )

    await store.put("cfg-1", cfg)

    assert store._redis.ttls["vca:percall:cfg-1"] == 42
    assert await store.get("cfg-1") == cfg
    assert await store.pop("cfg-1") == cfg
    assert await store.get("cfg-1") is None
    await store.close()


@pytest.mark.anyio
async def test_build_store_selects_backend() -> None:
    memory = build_per_call_config_store(_test_settings(session_backend="memory"))
    assert isinstance(memory, InMemoryPerCallConfigStore)

    redis_store = build_per_call_config_store(_test_settings(session_backend="redis"))
    assert isinstance(redis_store, RedisPerCallConfigStore)
    await redis_store.close()


# --- OutboundCallRequest ---------------------------------------------------
def test_outbound_request_accepts_overrides() -> None:
    req = OutboundCallRequest.model_validate(
        {"to": "+919876543210", "model": "claude-opus-4-8", "system_prompt": "You are Acme."}
    )
    assert req.model == "claude-opus-4-8"
    assert req.system_prompt == "You are Acme."


def test_outbound_request_blank_overrides_become_none() -> None:
    req = OutboundCallRequest.model_validate(
        {"to": "+919876543210", "model": "   ", "system_prompt": ""}
    )
    assert req.model is None
    assert req.system_prompt is None


# --- factory override ------------------------------------------------------
@pytest.mark.anyio
async def test_factory_applies_per_call_llm_overrides() -> None:
    runner = await build_media_session_runner(
        settings=_orchestrator_settings(),
        call_id="call-1",
        stream_id="stream-1",
        transport=_CapturingTransport(),
        llm_provider_override="openai",
        llm_model_override="gpt-4o",
        llm_system_prompt_override="You are Acme Dental.",
    )
    llm = runner._llm
    assert isinstance(llm, OrchestratorLLMClient)
    assert llm._provider == "openai"
    assert llm._model == "gpt-4o"
    assert llm._system_prompt == "You are Acme Dental."


@pytest.mark.anyio
async def test_factory_falls_back_to_env_without_overrides() -> None:
    runner = await build_media_session_runner(
        settings=_orchestrator_settings(),
        call_id="call-1",
        stream_id="stream-1",
        transport=_CapturingTransport(),
    )
    llm = runner._llm
    assert isinstance(llm, OrchestratorLLMClient)
    assert llm._model == "claude-haiku-4-5"
    assert llm._system_prompt == "env default prompt"


@pytest.mark.anyio
async def test_factory_applies_behavior_and_temperature() -> None:
    runner = await build_media_session_runner(
        settings=_orchestrator_settings(),
        call_id="call-1",
        stream_id="stream-1",
        transport=_CapturingTransport(),
        temperature_override=0.25,
        behavior_override={
            "turn_timeout_seconds": 7,
            "max_reply_chars": 321,
            "barge_in": False,
            "barge_in_energy_threshold": 450,
            "pre_response_ack_text": "Checking that now.",
            "pre_response_ack_delay_ms": 275,
            "fast_final_end_of_turn_silence_ms": 190,
            "speculative_response_prewarm": False,
            "speculative_response_stability_ms": 30,
            "speculative_response_min_chars": 12,
            "closing_message": "Thanks for calling Acme Dental. Goodbye.",
            "auto_hangup_after_closing": False,
        },
    )
    assert runner._turn_timeout_seconds == 7.0
    assert runner._max_reply_chars == 321
    assert runner._barge_in_energy_threshold == 450.0
    assert runner._pre_response_ack_text is None
    assert runner._pre_response_ack_delay_seconds == 0.275
    assert runner._turn_policy.fast_final_end_of_turn_silence_seconds == 0.19
    assert not runner._speculative_response_prewarm
    assert runner._speculative_response_stability_seconds == 0.03
    assert runner._speculative_response_min_chars == 12
    assert runner._closing_message == "Thanks for calling Acme Dental. Goodbye."
    assert runner._auto_hangup_after_closing is False
    # barge_in disabled -> min_frames set so the caller effectively cannot interrupt.
    assert runner._barge_in_min_frames >= 1_000_000_000
    llm = runner._llm
    assert isinstance(llm, OrchestratorLLMClient)
    assert llm._temperature == 0.25


@pytest.mark.anyio
async def test_factory_ignores_overlong_pre_response_ack_override() -> None:
    settings = _orchestrator_settings()
    runner = await build_media_session_runner(
        settings=settings,
        call_id="call-1",
        stream_id="stream-1",
        transport=_CapturingTransport(),
        behavior_override={"pre_response_ack_text": "x" * 91},
    )

    assert runner._pre_response_ack_text == settings.pre_response_ack_text


@pytest.mark.anyio
async def test_factory_applies_stt_tts_overrides() -> None:
    settings = Settings(
        environment="test",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="deepgram",
        deepgram_api_key="dg-key",
        llm_provider="local",
        tts_provider="sarvam",
        sarvam_api_key="sv-key",
        sarvam_streaming=False,
    )
    runner = await build_media_session_runner(
        settings=settings,
        call_id="c",
        stream_id="s",
        transport=_CapturingTransport(),
        stt_override={"language": "hi-IN", "endpointing_ms": 220, "utterance_end_ms": 1300},
        tts_override={"voice": "neha", "model": "bulbul:v2"},
    )
    assert isinstance(runner._stt, DeepgramStreamingSpeechToText)
    assert runner._stt._language == "hi-IN"
    assert runner._stt._endpointing_ms == 220
    assert runner._stt._utterance_end_ms == 1300
    assert isinstance(runner._tts, SarvamTextToSpeech)
    assert runner._tts._speaker == "neha"
    assert runner._tts._model == "bulbul:v2"
    await runner.close()


@pytest.mark.anyio
async def test_factory_ignores_invalid_stt_endpointing_overrides() -> None:
    settings = Settings(
        environment="test",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="deepgram",
        deepgram_api_key="dg-key",
        deepgram_endpointing_ms=260,
        deepgram_utterance_end_ms=1400,
        llm_provider="local",
        tts_provider="local",
    )
    runner = await build_media_session_runner(
        settings=settings,
        call_id="c",
        stream_id="s",
        transport=_CapturingTransport(),
        stt_override={"endpointing_ms": 0, "utterance_end_ms": 999},
    )

    assert isinstance(runner._stt, DeepgramStreamingSpeechToText)
    assert runner._stt._endpointing_ms == 260
    assert runner._stt._utterance_end_ms == 1400
    await runner.close()


# --- answer-URL carrier ----------------------------------------------------
def test_answer_ws_url_appends_cfg_without_token() -> None:
    url = _build_answer_media_websocket_url(_local_settings(), {}, "cfg-123")
    assert parse_qs(urlparse(url).query)["cfg"] == ["cfg-123"]


def test_answer_ws_url_without_cfg_is_unchanged() -> None:
    settings = _local_settings()
    url = _build_answer_media_websocket_url(settings, {}, None)
    assert "cfg=" not in url


def test_answer_ws_url_includes_both_token_and_cfg() -> None:
    settings = Settings(
        environment="test",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="local",
        tts_provider="local",
        stream_auth_token="stream-secret-0123456789abcdef012345",
    )
    url = _build_answer_media_websocket_url(settings, {"CallUUID": "call-9"}, "cfg-9")
    query = parse_qs(urlparse(url).query)
    assert query["cfg"] == ["cfg-9"]
    assert "token" in query


# --- /calls/outbound carries cfg -------------------------------------------
class _RecordingOutboundCallClient:
    def __init__(self) -> None:
        self.answer_urls: list[str] = []

    async def create_call(
        self,
        *,
        from_number: str,
        to_number: str,
        answer_url: str,
        answer_method: str,
        hangup_url: str,
        hangup_method: str,
        hangup_on_ring_seconds: int,
        time_limit_seconds: int,
    ) -> PlivoCallCreateResult:
        self.answer_urls.append(answer_url)
        del (
            from_number,
            to_number,
            answer_method,
            hangup_url,
            hangup_method,
            hangup_on_ring_seconds,
            time_limit_seconds,
        )
        return PlivoCallCreateResult(api_id="api-1", message="ok", request_uuid="request-1")

    async def hangup_call(self, identifier: str) -> None:
        del identifier


def _outbound_app(call_client: _RecordingOutboundCallClient) -> Any:
    return create_app(
        _local_settings(control_api_key=CONTROL_API_KEY, plivo_from_number="+14155550100"),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
        outbound_call_client=call_client,
    )


def test_outbound_call_carries_per_call_config_via_cfg() -> None:
    call_client = _RecordingOutboundCallClient()
    app = _outbound_app(call_client)
    client = TestClient(app)

    response = client.post(
        "/calls/outbound",
        headers={"Authorization": f"Bearer {CONTROL_API_KEY}", "Idempotency-Key": "per-call-1"},
        json={
            "to": "+919876543210",
            "provider": "anthropic",
            "model": "claude-opus-4-8",
            "system_prompt": "You are Acme.",
        },
    )

    assert response.status_code == 202
    cfg_values = parse_qs(urlparse(call_client.answer_urls[0]).query).get("cfg")
    assert cfg_values is not None
    stored = asyncio.run(app.state.per_call_config_store.get(cfg_values[0]))
    assert stored == PerCallLLMConfig(
        provider="anthropic", model="claude-opus-4-8", system_prompt="You are Acme."
    )


def test_outbound_call_without_overrides_has_no_cfg() -> None:
    call_client = _RecordingOutboundCallClient()
    app = _outbound_app(call_client)
    client = TestClient(app)

    response = client.post(
        "/calls/outbound",
        headers={"Authorization": f"Bearer {CONTROL_API_KEY}", "Idempotency-Key": "per-call-2"},
        json={"to": "+919876543210"},
    )

    assert response.status_code == 202
    assert "cfg=" not in call_client.answer_urls[0]


# --- media WS applies the override end to end ------------------------------
class _FakeRunner:
    def __init__(self, call_id: str, stream_id: str, transport: Any = None) -> None:
        self._call_id = call_id
        self._stream_id = stream_id
        self._transport = transport
        self.started = False
        self.greeted = False

    @property
    def call_id(self) -> str:
        return self._call_id

    @property
    def stream_id(self) -> str:
        return self._stream_id

    async def accept_audio(self, audio: bytes) -> None:
        del audio

    async def start(self) -> None:
        self.started = True

    async def greet(self) -> None:
        self.greeted = True
        if self._transport is not None:
            await self._transport.send_json({"event": "marker"})

    async def close(self) -> None:
        return None


def test_media_ws_applies_per_call_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(
        _local_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
    )
    asyncio.run(
        app.state.per_call_config_store.put(
            "cfg-xyz",
            PerCallLLMConfig(
                provider="anthropic",
                model="claude-opus-4-8",
                system_prompt="You are Acme Dental.",
                greeting="Hello there!",
                temperature=0.4,
                behavior={"turn_timeout_seconds": 8},
            ),
        )
    )
    captured: dict[str, Any] = {}

    async def fake_build(
        *,
        settings: Settings,
        call_id: str,
        stream_id: str,
        transport: Any,
        llm_provider_override: str | None = None,
        llm_model_override: str | None = None,
        llm_system_prompt_override: str | None = None,
        greeting_override: str | None = None,
        temperature_override: float | None = None,
        behavior_override: Any = None,
        stt_override: Any = None,
        tts_override: Any = None,
        latency_observer: Any = None,
        transcript_observer: Any = None,
        voice_action_observer: Any = None,
        hangup_observer: Any = None,
    ) -> _FakeRunner:
        captured["provider"] = llm_provider_override
        captured["model"] = llm_model_override
        captured["system_prompt"] = llm_system_prompt_override
        captured["greeting"] = greeting_override
        captured["temperature"] = temperature_override
        captured["behavior"] = behavior_override
        captured["latency_observer"] = latency_observer
        captured["transcript_observer"] = transcript_observer
        captured["voice_action_observer"] = voice_action_observer
        captured["hangup_observer"] = hangup_observer
        runner = _FakeRunner(call_id, stream_id, transport)
        captured["runner"] = runner
        return runner

    monkeypatch.setattr("voice_agent.app.build_media_session_runner", fake_build)

    client = TestClient(app)
    with client.websocket_connect("/plivo/media?cfg=cfg-xyz") as websocket:
        websocket.send_json(
            {"event": "start", "start": {"call_uuid": "call-1", "stream_id": "stream-1"}}
        )
        marker = websocket.receive_json()
        assert marker["event"] == "marker"
        websocket.send_json({"event": "stop"})

    assert captured["provider"] == "anthropic"
    assert captured["model"] == "claude-opus-4-8"
    assert captured["system_prompt"] == "You are Acme Dental."
    assert captured["greeting"] == "Hello there!"
    assert captured["temperature"] == 0.4
    assert captured["behavior"] == {"turn_timeout_seconds": 8}
    assert captured["latency_observer"] is not None
    assert captured["voice_action_observer"] is not None
    assert captured["hangup_observer"] is None
    assert captured["runner"].started is True
    assert captured["runner"].greeted is True


# --- inbound: control-plane lookup + answer resolution ---------------------
@pytest.mark.anyio
async def test_fetch_inbound_config_returns_overrides() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ingest/inbound-config"
        assert request.url.params["number"] == "+918040001234"
        assert request.headers["X-API-Key"] == "proj-key"
        return httpx.Response(
            200,
            json={
                "provider": "anthropic",
                "model": "claude-opus-4-8",
                "system_prompt": "You are Reception.",
            },
        )

    client = ControlPlaneClient("http://cp.local", "proj-key")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        config = await client.fetch_inbound_config("+918040001234")
    finally:
        await client.close()

    assert config == PerCallLLMConfig(
        provider="anthropic", model="claude-opus-4-8", system_prompt="You are Reception."
    )


@pytest.mark.anyio
async def test_fetch_inbound_config_empty_returns_none() -> None:
    client = ControlPlaneClient("http://cp.local", "k")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _r: httpx.Response(200, json={"model": None, "system_prompt": None})
        )
    )
    try:
        assert await client.fetch_inbound_config("+918040001234") is None
    finally:
        await client.close()


@pytest.mark.anyio
async def test_fetch_inbound_config_http_error_returns_none() -> None:
    client = ControlPlaneClient("http://cp.local", "k")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _r: httpx.Response(404))
    )
    try:
        assert await client.fetch_inbound_config("+918040001234") is None
    finally:
        await client.close()


class _FakeControlPlaneClient:
    def __init__(self, config: PerCallLLMConfig | None) -> None:
        self._config = config
        self.requested_numbers: list[str] = []

    async def fetch_inbound_config(
        self, number: str, *, timeout_seconds: float = 2.5
    ) -> PerCallLLMConfig | None:
        del timeout_seconds
        self.requested_numbers.append(number)
        return self._config


def _cfg_from_answer_xml(xml: str) -> str | None:
    match = re.search(r"cfg=([A-Za-z0-9_-]+)", xml)
    return match.group(1) if match else None


def _answer_app() -> Any:
    return create_app(
        _local_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
    )


def test_plivo_answer_inbound_resolves_per_call_config() -> None:
    app = _answer_app()
    cfg = PerCallLLMConfig(model="claude-opus-4-8", system_prompt="You are Reception.")
    fake = _FakeControlPlaneClient(cfg)
    app.state.control_plane_client = fake
    client = TestClient(app)

    response = client.post("/plivo/answer", data={"Direction": "inbound", "To": "+918040001234"})

    assert response.status_code == 200
    cfg_id = _cfg_from_answer_xml(response.text)
    assert cfg_id is not None
    assert asyncio.run(app.state.per_call_config_store.get(cfg_id)) == cfg
    assert fake.requested_numbers == ["+918040001234"]


def test_plivo_answer_inbound_disabled_returns_spoken_hangup() -> None:
    app = _answer_app()
    fake = _FakeControlPlaneClient(
        PerCallLLMConfig(
            behavior={
                "inbound_enabled": False,
                "closing_message": "We are not taking inbound calls right now. Goodbye.",
            },
        )
    )
    app.state.control_plane_client = fake
    client = TestClient(app)

    response = client.post("/plivo/answer", data={"Direction": "inbound", "To": "+918040001234"})

    assert response.status_code == 200
    assert "<Speak>We are not taking inbound calls right now. Goodbye.</Speak>" in response.text
    assert "<Hangup/>" in response.text
    assert _cfg_from_answer_xml(response.text) is None
    assert fake.requested_numbers == ["+918040001234"]


def test_plivo_answer_outbound_direction_does_not_resolve_inbound() -> None:
    app = _answer_app()
    fake = _FakeControlPlaneClient(PerCallLLMConfig(model="claude-opus-4-8"))
    app.state.control_plane_client = fake
    client = TestClient(app)

    response = client.post("/plivo/answer", data={"Direction": "outbound", "To": "+918040001234"})

    assert response.status_code == 200
    assert _cfg_from_answer_xml(response.text) is None
    assert fake.requested_numbers == []


def test_plivo_answer_without_control_plane_client_skips_inbound() -> None:
    app = _answer_app()
    client = TestClient(app)

    response = client.post("/plivo/answer", data={"Direction": "inbound", "To": "+918040001234"})

    assert response.status_code == 200
    assert _cfg_from_answer_xml(response.text) is None
