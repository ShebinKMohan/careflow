import asyncio
import base64
import hashlib
import hmac
import json
import queue
import threading
import time
from types import SimpleNamespace
from typing import Any, Protocol, cast

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from voice_agent.app import (
    _deregister_media_runner,
    _drain_active_media_runners,
    _is_valid_plivo_websocket_upgrade,
    _register_media_runner,
    create_app,
)
from voice_agent.config.settings import Settings
from voice_agent.security.stream_token import build_stream_token
from voice_agent.storage.call_events import InMemoryCallEventStore


class JsonReceivingWebSocket(Protocol):
    def receive_json(self) -> object: ...


def test_plivo_media_websocket_processes_start_media_and_stop() -> None:
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
        )
    )
    client = TestClient(app)

    with client.websocket_connect("/plivo/media") as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-1",
                    "stream_id": "stream-1",
                },
            }
        )
        websocket.send_json(
            {
                "event": "media",
                "media": {
                    "payload": base64.b64encode(b"hello!").decode("ascii"),
                },
            }
        )

        response = websocket.receive_json()

        assert response["event"] == "playAudio"
        assert response["media"]["contentType"] == "audio/x-l16"
        assert response["media"]["sampleRate"] == 16000

        websocket.send_json({"event": "stop"})


def test_plivo_media_websocket_records_voice_turn_latency_event() -> None:
    event_store = InMemoryCallEventStore()
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
        ),
        call_event_store=event_store,
    )
    client = TestClient(app)

    with client.websocket_connect("/plivo/media") as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-1",
                    "stream_id": "stream-1",
                },
            }
        )
        websocket.send_json(
            {
                "event": "media",
                "media": {
                    "payload": base64.b64encode(b"hello!").decode("ascii"),
                },
            }
        )

        response = websocket.receive_json()
        assert response["event"] == "playAudio"

        event = _wait_for_event(event_store, event_name="voice_turn_latency")
        assert event["call_id"] == "call-1"
        assert event["stream_id"] == "stream-1"
        assert isinstance(event["turn_epoch"], int)
        assert event["transcript_chars"] == len("hello!")
        assert event["turn_commit_source"] == "synchronous_stt"
        assert event["response_plan_action"] == "streaming_llm"
        assert event["response_plan_confidence"] == 1.0
        assert event["response_plan_reason"] == "default_open_ended_path"
        assert event["first_audio_to_first_play_audio_ms"] is not None
        assert event["turn_committed_to_llm_first_chunk_ms"] is not None
        assert event["turn_committed_to_first_reply_chunk_ms"] is not None
        assert event["llm_first_chunk_to_first_reply_chunk_ms"] is not None
        assert event["llm_first_chunk_to_tts_first_audio_ms"] is not None
        assert event["first_reply_chunk_to_tts_first_audio_ms"] is not None
        assert "answer_first_play_audio_to_llm_stream_completed_ms" in event

        websocket.send_json({"event": "stop"})


def test_plivo_media_websocket_records_voice_action_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voice_agent.providers import factory as provider_factory
    from voice_agent.providers.local import LocalLLMClient

    def build_action_llm(*_args: object, **_kwargs: object) -> LocalLLMClient:
        return LocalLLMClient(
            response_text=(
                "Your appointment is booked. "
                '[[ACTION:{"type":"appointment.confirm",'
                '"payload":{"appointment_id":"a-1"}}]]'
            )
        )

    monkeypatch.setattr(provider_factory, "_build_llm", build_action_llm)
    event_store = InMemoryCallEventStore()
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
        ),
        call_event_store=event_store,
    )
    client = TestClient(app)

    with client.websocket_connect("/plivo/media") as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-1",
                    "stream_id": "stream-1",
                },
            }
        )
        websocket.send_json(
            {
                "event": "media",
                "media": {
                    "payload": base64.b64encode(b"hello!").decode("ascii"),
                },
            }
        )

        response = websocket.receive_json()
        assert response["event"] == "playAudio"

        event = _wait_for_event(event_store, event_name="voice_action_requested")
        assert event["call_id"] == "call-1"
        assert event["stream_id"] == "stream-1"
        assert isinstance(event["turn_epoch"], int)
        assert event["action_type"] == "appointment.confirm"
        assert event["action_payload"] == {"appointment_id": "a-1"}
        assert isinstance(event["recorded_at_ms"], int)

        websocket.send_json({"event": "stop"})


def test_plivo_media_websocket_latency_event_store_failure_does_not_break_call() -> None:
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
        ),
        call_event_store=FailingRecordCallEventStore(),
    )
    client = TestClient(app)

    with client.websocket_connect("/plivo/media") as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-1",
                    "stream_id": "stream-1",
                },
            }
        )
        websocket.send_json(
            {
                "event": "media",
                "media": {
                    "payload": base64.b64encode(b"hello!").decode("ascii"),
                },
            }
        )

        response = websocket.receive_json()
        assert response["event"] == "playAudio"

        websocket.send_json({"event": "stop"})


def test_plivo_media_websocket_signature_validation_rejects_non_ascii_query_string() -> None:
    settings = Settings(
        environment="test",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="local",
        tts_provider="local",
        plivo_auth_token="plivo-auth-token",
    )
    websocket = cast(
        Any,
        SimpleNamespace(
            scope={"query_string": b"token=\xff"},
            headers={
                "X-Plivo-Signature-V3": "invalid-signature",
                "X-Plivo-Signature-V3-Nonce": "nonce-123",
            },
            url=SimpleNamespace(path="/plivo/media"),
        ),
    )

    assert _is_valid_plivo_websocket_upgrade(websocket, settings) is False


def test_plivo_media_websocket_closes_when_start_event_never_arrives() -> None:
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            media_start_timeout_seconds=0.01,
        )
    )
    client = TestClient(app)
    results: queue.Queue[int | Exception] = queue.Queue()

    with client.websocket_connect("/plivo/media") as websocket:
        receiver = threading.Thread(
            target=_receive_disconnect_code,
            args=(websocket, results),
            daemon=True,
        )
        receiver.start()
        receiver.join(timeout=0.3)
        try:
            assert not receiver.is_alive()
            result = results.get_nowait()
            assert result == 1008
        finally:
            websocket.close()


def test_plivo_media_websocket_rejects_duplicate_start_event() -> None:
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
        )
    )
    client = TestClient(app)

    with client.websocket_connect("/plivo/media") as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-1",
                    "stream_id": "stream-1",
                },
            }
        )
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-1",
                    "stream_id": "stream-2",
                },
            }
        )
        websocket.send_json(
            {
                "event": "media",
                "media": {
                    "payload": base64.b64encode(b"hello!").decode("ascii"),
                },
            }
        )

        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert exc_info.value.code == 1008


def test_plivo_media_websocket_rejects_media_for_different_stream_id() -> None:
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
        )
    )
    client = TestClient(app)

    with client.websocket_connect("/plivo/media") as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-1",
                    "stream_id": "stream-1",
                },
            }
        )
        websocket.send_json(
            {
                "event": "media",
                "streamId": "stream-2",
                "media": {
                    "payload": base64.b64encode(b"hello!").decode("ascii"),
                },
            }
        )

        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert exc_info.value.code == 1008


def test_plivo_media_websocket_rejects_oversized_media_frame_before_parsing() -> None:
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            media_message_max_bytes=128,
        )
    )
    client = TestClient(app)
    oversized_media_message = json.dumps(
        {
            "event": "media",
            "streamId": "stream-1",
            "media": {
                "payload": base64.b64encode(b"hello!").decode("ascii"),
            },
            "padding": "x" * 256,
        }
    )

    with client.websocket_connect("/plivo/media") as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-1",
                    "stream_id": "stream-1",
                },
            }
        )
        websocket.send_text(oversized_media_message)

        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert exc_info.value.code == 1008


def test_plivo_media_websocket_rejects_binary_frames_as_protocol_errors() -> None:
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
        )
    )
    client = TestClient(app)

    with client.websocket_connect("/plivo/media") as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-1",
                    "stream_id": "stream-1",
                },
            }
        )
        websocket.send_bytes(b'{"event":"stop"}')

        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert exc_info.value.code == 1008


def test_plivo_media_websocket_rejects_blank_start_identifiers() -> None:
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
        )
    )
    client = TestClient(app)

    with client.websocket_connect("/plivo/media") as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "   ",
                    "stream_id": "stream-1",
                },
            }
        )

        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert exc_info.value.code == 1008


def test_plivo_media_websocket_closes_and_cleans_up_when_runner_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken_runner = BrokenMediaRunner()

    async def build_broken_runner(**kwargs: object) -> BrokenMediaRunner:
        del kwargs
        return broken_runner

    monkeypatch.setattr("voice_agent.app.build_media_session_runner", build_broken_runner)
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
        )
    )
    client = TestClient(app)

    with client.websocket_connect("/plivo/media") as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-1",
                    "stream_id": "stream-1",
                },
            }
        )
        websocket.send_json(
            {
                "event": "media",
                "streamId": "stream-1",
                "media": {
                    "payload": base64.b64encode(b"hello!").decode("ascii"),
                },
            }
        )

        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert exc_info.value.code == 1011
    assert broken_runner.closed


def test_plivo_media_websocket_contains_runner_close_failure_on_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closing_failure_runner = ClosingFailureMediaRunner()

    async def build_closing_failure_runner(**kwargs: object) -> ClosingFailureMediaRunner:
        del kwargs
        return closing_failure_runner

    monkeypatch.setattr(
        "voice_agent.app.build_media_session_runner",
        build_closing_failure_runner,
    )
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
        )
    )
    client = TestClient(app)

    with client.websocket_connect("/plivo/media") as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-1",
                    "stream_id": "stream-1",
                },
            }
        )
        websocket.send_json({"event": "stop"})

    assert closing_failure_runner.close_attempted


def test_production_media_websocket_rejects_missing_stream_token() -> None:
    app = create_app(
        Settings(
            environment="production",
            public_base_url="https://voice.example.com",
            session_backend="redis",
            redis_url="redis://redis:6379/0",
            stt_provider="deepgram",
            deepgram_api_key="deepgram-secret",
            llm_provider="orchestrator",
            orchestrator_url="https://orchestrator.example.com/respond",
            orchestrator_api_key="orchestrator-secret",
            tts_provider="elevenlabs",
            elevenlabs_api_key="eleven-secret",
            elevenlabs_voice_id="voice-1",
            control_api_key="control-secret-0123456789abcdef012345",
            plivo_auth_id="plivo-auth-id",
            plivo_auth_token="plivo-auth-token",
            plivo_from_number="+14155550100",
            stream_auth_token="stream-secret-0123456789abcdef012345",
        )
    )
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as exc_info, client.websocket_connect("/plivo/media"):
        pass

    assert exc_info.value.code == 1008


def test_production_media_websocket_rejects_raw_stream_secret() -> None:
    app = create_app(_production_settings(), call_event_store=InMemoryCallEventStore())
    client = TestClient(app)
    path = "/plivo/media?token=stream-secret-0123456789abcdef012345"

    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(
            path,
            headers=_plivo_websocket_headers(path),
        ),
    ):
        pass

    assert exc_info.value.code == 1008


def test_production_media_websocket_rejects_tampered_stream_token_before_start() -> None:
    app = create_app(_production_settings(), call_event_store=InMemoryCallEventStore())
    client = TestClient(app)
    path = "/plivo/media?token=v1.4102445100.call-1.nonce-1.invalid"

    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(
            path,
            headers=_plivo_websocket_headers(path),
        ),
    ):
        pass

    assert exc_info.value.code == 1008


def test_production_media_websocket_accepts_valid_stream_token_without_plivo_signature() -> None:
    # With a stream_auth_token configured, a valid stream token is the media-WS auth; the
    # Plivo WS request signature (which cannot be reliably reconstructed behind proxies/
    # tunnels, and breaks real calls when required) is NOT independently required.
    app = create_app(_production_settings(), call_event_store=InMemoryCallEventStore())
    client = TestClient(app)
    token = build_stream_token(
        signing_secret="stream-secret-0123456789abcdef012345",
        ttl_seconds=300,
        now=4_102_444_800,
        nonce="nonce-1",
        call_id="call-1",
    )

    with client.websocket_connect(f"/plivo/media?token={token}") as websocket:
        websocket.send_json(
            {"event": "start", "start": {"call_uuid": "call-1", "stream_id": "stream-1"}}
        )
        websocket.send_json({"event": "stop"})


def test_production_media_websocket_accepts_valid_token_despite_invalid_plivo_signature() -> None:
    app = create_app(_production_settings(), call_event_store=InMemoryCallEventStore())
    client = TestClient(app)
    token = build_stream_token(
        signing_secret="stream-secret-0123456789abcdef012345",
        ttl_seconds=300,
        now=4_102_444_800,
        nonce="nonce-1",
        call_id="call-1",
    )

    with client.websocket_connect(
        f"/plivo/media?token={token}",
        headers={
            "X-Plivo-Signature-V3": "invalid",
            "X-Plivo-Signature-V3-Nonce": "plivo-nonce-1",
        },
    ) as websocket:
        websocket.send_json(
            {"event": "start", "start": {"call_uuid": "call-1", "stream_id": "stream-1"}}
        )
        websocket.send_json({"event": "stop"})


def test_production_media_websocket_accepts_signed_stream_token() -> None:
    app = create_app(_production_settings(), call_event_store=InMemoryCallEventStore())
    client = TestClient(app)
    token = build_stream_token(
        signing_secret="stream-secret-0123456789abcdef012345",
        ttl_seconds=300,
        now=4_102_444_800,
        nonce="nonce-1",
        call_id="call-1",
    )
    path = f"/plivo/media?token={token}"

    with client.websocket_connect(path, headers=_plivo_websocket_headers(path)) as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-1",
                    "stream_id": "stream-1",
                },
            }
        )
        websocket.send_json({"event": "stop"})


def test_production_media_websocket_rejects_start_with_mismatched_call_token() -> None:
    app = create_app(_production_settings(), call_event_store=InMemoryCallEventStore())
    client = TestClient(app)
    token = build_stream_token(
        signing_secret="stream-secret-0123456789abcdef012345",
        ttl_seconds=300,
        now=4_102_444_800,
        nonce="nonce-1",
        call_id="call-1",
    )
    path = f"/plivo/media?token={token}"

    with client.websocket_connect(path, headers=_plivo_websocket_headers(path)) as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-2",
                    "stream_id": "stream-1",
                },
            }
        )

        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert exc_info.value.code == 1008


def test_production_media_websocket_uses_constant_time_stream_signature_compare(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(_production_settings(), call_event_store=InMemoryCallEventStore())
    client = TestClient(app)
    compared_signatures: list[tuple[str, str]] = []

    def compare_digest(left: str, right: str) -> bool:
        compared_signatures.append((left, right))
        return True

    monkeypatch.setattr("voice_agent.security.stream_token.hmac.compare_digest", compare_digest)
    path = "/plivo/media?token=v1.4102445100.call-1.nonce-1.invalid"

    with client.websocket_connect(
        path,
        headers=_plivo_websocket_headers(path),
    ) as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-1",
                    "stream_id": "stream-1",
                },
            }
        )
        websocket.send_json({"event": "stop"})

    assert compared_signatures
    stream_token_comparison = next(
        comparison for comparison in compared_signatures if comparison[0] == "invalid"
    )
    assert stream_token_comparison[1] != "stream-secret-0123456789abcdef012345"


def _production_settings() -> Settings:
    return Settings(
        environment="production",
        public_base_url="https://voice.example.com",
        session_backend="redis",
        redis_url="redis://redis:6379/0",
        stt_provider="deepgram",
        deepgram_api_key="deepgram-secret",
        llm_provider="orchestrator",
        orchestrator_url="https://orchestrator.example.com/respond",
        orchestrator_api_key="orchestrator-secret",
        tts_provider="elevenlabs",
        elevenlabs_api_key="eleven-secret",
        elevenlabs_voice_id="voice-1",
        control_api_key="control-secret-0123456789abcdef012345",
        plivo_auth_id="plivo-auth-id",
        plivo_auth_token="plivo-auth-token",
        plivo_from_number="+14155550100",
        stream_auth_token="stream-secret-0123456789abcdef012345",
    )


def _plivo_websocket_headers(path: str) -> dict[str, str]:
    nonce = "plivo-nonce-1"
    signed_payload = f"GEThttps://voice.example.com{path}{nonce}"
    signature = base64.b64encode(
        hmac.new(
            b"plivo-auth-token",
            signed_payload.encode("utf-8"),
            hashlib.sha256,
        ).digest()
    ).decode("ascii")
    return {
        "X-Plivo-Signature-V3": signature,
        "X-Plivo-Signature-V3-Nonce": nonce,
    }


class BrokenMediaRunner:
    call_id = "call-1"
    stream_id = "stream-1"

    def __init__(self) -> None:
        self.closed = False

    async def start(self) -> None:
        return None

    async def greet(self) -> None:
        return None

    async def accept_audio(self, audio: bytes) -> None:
        del audio
        raise RuntimeError("media provider unavailable")

    async def close(self) -> None:
        self.closed = True


class ClosingFailureMediaRunner:
    call_id = "call-1"
    stream_id = "stream-1"

    def __init__(self) -> None:
        self.close_attempted = False

    async def start(self) -> None:
        return None

    async def greet(self) -> None:
        return None

    async def accept_audio(self, audio: bytes) -> None:
        del audio

    async def close(self) -> None:
        self.close_attempted = True
        raise RuntimeError("media runner close failed")


class FailingRecordCallEventStore:
    async def record(self, event: dict[str, Any]) -> None:
        del event
        raise RuntimeError("call event storage is unavailable")

    async def recent(self, *, count: int) -> list[dict[str, Any]]:
        del count
        return []

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


def _wait_for_event(
    event_store: InMemoryCallEventStore,
    *,
    event_name: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        for event in event_store.events:
            if event.get("event") == event_name:
                return event
        time.sleep(0.01)
    raise AssertionError(f"event {event_name!r} was not recorded")


def _receive_disconnect_code(
    websocket: JsonReceivingWebSocket,
    results: queue.Queue[int | Exception],
) -> None:
    try:
        websocket.receive_json()
    except WebSocketDisconnect as exc:
        results.put(exc.code)
    except Exception as exc:  # pragma: no cover - failure detail for receiver thread.
        results.put(exc)


class FailingUpsertCallSessionStore:
    """Call-session store whose upsert() always fails, simulating a transient Redis
    outage during the media-start lifecycle write."""

    async def upsert(self, update: Any) -> dict[str, Any]:
        del update
        raise RuntimeError("call session storage is unavailable")

    async def reserve_idempotency_key(self, update: Any) -> dict[str, Any] | None:
        del update
        return None

    async def get(self, identifier: str) -> dict[str, Any] | None:
        del identifier
        return None

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


def test_plivo_media_websocket_session_upsert_failure_does_not_break_call() -> None:
    # Regression: the media-start bookkeeping upsert was awaited unprotected, so a transient
    # Redis failure closed the socket (1011) and dropped a live call. It must be best-effort.
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
        ),
        call_session_store=cast(Any, FailingUpsertCallSessionStore()),
    )
    client = TestClient(app)

    with client.websocket_connect("/plivo/media") as websocket:
        websocket.send_json(
            {"event": "start", "start": {"call_uuid": "call-1", "stream_id": "stream-1"}}
        )
        websocket.send_json(
            {"event": "media", "media": {"payload": base64.b64encode(b"hello!").decode("ascii")}}
        )

        # The call must still produce audio despite every session-store write failing.
        response = websocket.receive_json()
        assert response["event"] == "playAudio"

        websocket.send_json({"event": "stop"})


def _local_settings() -> Settings:
    return Settings(
        environment="test",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="local",
        tts_provider="local",
    )


class FakeActiveRunner:
    """Minimal ActiveMediaRunner used to exercise shutdown draining."""

    def __init__(self) -> None:
        self.closed = False

    @property
    def call_id(self) -> str:
        return "call-x"

    @property
    def stream_id(self) -> str:
        return "stream-x"

    async def close(self) -> None:
        self.closed = True


def test_plivo_media_websocket_refuses_new_calls_while_shutting_down() -> None:
    app = create_app(_local_settings())
    app.state.shutting_down = True
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as exc_info, client.websocket_connect("/plivo/media"):
        pass

    assert exc_info.value.code == 1013


async def test_drain_active_media_runners_force_closes_stragglers() -> None:
    app = create_app(_local_settings())
    runner = FakeActiveRunner()
    _register_media_runner(app, cast(Any, runner))

    # The runner never finishes on its own, so after the grace window it must be force-closed
    # (releasing its provider clients) and the registry cleared.
    await _drain_active_media_runners(app, grace_seconds=0.05)

    assert runner.closed is True
    assert app.state.active_media_runners == set()


async def test_drain_active_media_runners_waits_for_natural_drain() -> None:
    app = create_app(_local_settings())
    runner = FakeActiveRunner()
    _register_media_runner(app, cast(Any, runner))

    async def finish_soon() -> None:
        await asyncio.sleep(0.02)
        _deregister_media_runner(app, cast(Any, runner))

    task = asyncio.create_task(finish_soon())
    await _drain_active_media_runners(app, grace_seconds=5.0)
    await task

    # It drained on its own well within the grace window, so it must NOT have been force-closed.
    assert runner.closed is False
    assert app.state.active_media_runners == set()


def test_metrics_counts_completed_turn() -> None:
    app = create_app(_local_settings())
    client = TestClient(app)

    with client.websocket_connect("/plivo/media") as websocket:
        websocket.send_json(
            {"event": "start", "start": {"call_uuid": "call-1", "stream_id": "stream-1"}}
        )
        websocket.send_json(
            {"event": "media", "media": {"payload": base64.b64encode(b"hello!").decode("ascii")}}
        )
        assert websocket.receive_json()["event"] == "playAudio"
        websocket.send_json({"event": "stop"})

    response = client.get("/metrics")
    assert "voice_agent_turns_total 1" in response.text


def test_stream_token_single_use_rejects_replay() -> None:
    secret = "stream-secret-0123456789abcdef012345"
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            stream_auth_token=secret,
            stream_token_single_use=True,
        )
    )
    token = build_stream_token(signing_secret=secret, ttl_seconds=300, call_id="call-1", cfg="")
    client = TestClient(app)

    # First use of the token is accepted.
    with client.websocket_connect(f"/plivo/media?token={token}") as websocket:
        websocket.send_json(
            {"event": "start", "start": {"call_uuid": "call-1", "stream_id": "stream-1"}}
        )
        websocket.send_json({"event": "stop"})

    # Replaying the same token is rejected before the socket is accepted.
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(f"/plivo/media?token={token}"),
    ):
        pass
    assert exc_info.value.code == 1008
