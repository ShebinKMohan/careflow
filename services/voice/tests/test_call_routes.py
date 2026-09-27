import asyncio
import base64
import io
import re
import wave
from collections.abc import AsyncIterator
from typing import Any, cast

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from voice_agent.app import (
    _read_callback_payload,
    _reject_oversized_callback_content_length,
    _reject_oversized_control_content_length,
    _resolve_public_callback_url,
    _validate_control_request,
    create_app,
)
from voice_agent.config.settings import Settings
from voice_agent.per_call_config import InMemoryPerCallConfigStore
from voice_agent.security.plivo_signature import build_v3_signature
from voice_agent.storage.call_events import InMemoryCallEventStore
from voice_agent.storage.call_sessions import InMemoryCallSessionStore
from voice_agent.telephony.plivo_client import PlivoCallClientError, PlivoCallCreateResult


def test_plivo_answer_route_returns_xml_stream_instruction() -> None:
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

    response = client.post("/plivo/answer")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert '<Stream bidirectional="true"' in response.text
    assert "wss://voice.example.com/plivo/media" in response.text


def test_plivo_answer_route_applies_stream_status_and_noise_settings() -> None:
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            stream_status_callback_method="POST",
            stream_noise_cancellation=True,
            stream_noise_cancellation_level=90,
        )
    )
    client = TestClient(app)

    response = client.post("/plivo/answer")

    assert response.status_code == 200
    assert 'statusCallbackMethod="POST"' in response.text
    assert 'noiseCancellation="true"' in response.text
    assert 'noiseCancellationLevel="90"' in response.text


def test_plivo_answer_route_returns_503_when_session_update_fails() -> None:
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
        call_session_store=FailingUpsertCallSessionStore(),
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/plivo/answer",
        json={"CallUUID": "call-1"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Call session storage is unavailable."
    assert not response.headers["content-type"].startswith("application/xml")
    assert event_store.events == [{"CallUUID": "call-1"}]


def test_plivo_answer_callback_is_recorded_for_audit() -> None:
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

    response = client.post(
        "/plivo/answer",
        json={"CallUUID": "call-1", "Event": "StartApp"},
    )

    assert response.status_code == 200
    assert event_store.events == [{"CallUUID": "call-1", "Event": "StartApp"}]


def test_plivo_answer_callback_returns_503_when_event_store_record_fails() -> None:
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
        call_session_store=InMemoryCallSessionStore(),
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/plivo/answer",
        json={"CallUUID": "call-1", "Event": "StartApp"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Call event storage is unavailable."
    assert client.get("/calls/call-1").status_code == 404
    assert not response.headers["content-type"].startswith("application/xml")


def test_health_routes_report_ready_for_local_stack() -> None:
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

    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {
        "status": "ready",
        "providers": {"stt": "local", "llm": "local", "tts": "local"},
        "public_base_url": "https://voice.example.com",
        "checks": {
            "call_event_store": "ok",
            "call_session_store": "ok",
            "per_call_config_store": "ok",
            "accepting_calls": "yes",
        },
    }


def test_readyz_reports_luxtts_sidecar_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_luxtts_sidecar_readiness(
        settings: Settings,
    ) -> tuple[bool, dict[str, object]]:
        assert settings.luxtts_tts_url == "https://luxtts.example.com/tts"
        return True, {
            "status": "ready",
            "ok": True,
            "loaded": True,
            "sidecar_instance_id": "sidecar-proof-instance-1",
            "sidecar_source_sha256": "a" * 64,
            "model_repo_id": "YatharthS/LuxTTS",
            "model_revision": "c" * 40,
            "model_artifact_sha256": "e" * 64,
            "model_artifact_file_count": 7,
            "device": "cuda",
            "requested_device": "cuda",
        }

    monkeypatch.setattr(
        "voice_agent.app._luxtts_sidecar_readiness",
        fake_luxtts_sidecar_readiness,
    )
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="luxtts",
            luxtts_tts_url="https://luxtts.example.com/tts",
        )
    )
    client = TestClient(app)

    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "providers": {"stt": "local", "llm": "local", "tts": "luxtts"},
        "public_base_url": "https://voice.example.com",
        "checks": {
            "call_event_store": "ok",
            "call_session_store": "ok",
            "per_call_config_store": "ok",
            "accepting_calls": "yes",
            "luxtts_sidecar": "ok",
        },
        "provider_details": {
            "luxtts_sidecar": {
                "status": "ready",
                "ok": True,
                "loaded": True,
                "sidecar_instance_id": "sidecar-proof-instance-1",
                "sidecar_source_sha256": "a" * 64,
                "model_repo_id": "YatharthS/LuxTTS",
                "model_revision": "c" * 40,
                "model_artifact_sha256": "e" * 64,
                "model_artifact_file_count": 7,
                "device": "cuda",
                "requested_device": "cuda",
            }
        },
    }


def test_readyz_reports_unready_when_luxtts_sidecar_is_unhealthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_luxtts_sidecar_readiness(
        _settings: Settings,
    ) -> tuple[bool, dict[str, object]]:
        return False, {"status": "http_error", "http_status": 503}

    monkeypatch.setattr(
        "voice_agent.app._luxtts_sidecar_readiness",
        fake_luxtts_sidecar_readiness,
    )
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="luxtts",
            luxtts_tts_url="https://luxtts.example.com/tts",
        )
    )
    client = TestClient(app)

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "providers": {"stt": "local", "llm": "local", "tts": "luxtts"},
        "public_base_url": "https://voice.example.com",
        "checks": {
            "call_event_store": "ok",
            "call_session_store": "ok",
            "per_call_config_store": "ok",
            "accepting_calls": "yes",
            "luxtts_sidecar": "unhealthy",
        },
        "provider_details": {"luxtts_sidecar": {"status": "http_error", "http_status": 503}},
    }


def test_readyz_reports_unready_when_call_event_store_health_check_fails() -> None:
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
        ),
        call_event_store=FailingCallEventStore(),
    )
    client = TestClient(app)

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "providers": {"stt": "local", "llm": "local", "tts": "local"},
        "public_base_url": "https://voice.example.com",
        "checks": {
            "call_event_store": "unhealthy",
            "call_session_store": "ok",
            "per_call_config_store": "ok",
            "accepting_calls": "yes",
        },
    }


def test_readyz_reports_unready_when_call_event_store_health_check_raises() -> None:
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
        ),
        call_event_store=RaisingHealthCheckCallEventStore(),
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "providers": {"stt": "local", "llm": "local", "tts": "local"},
        "public_base_url": "https://voice.example.com",
        "checks": {
            "call_event_store": "unhealthy",
            "call_session_store": "ok",
            "per_call_config_store": "ok",
            "accepting_calls": "yes",
        },
    }


def test_readyz_reports_unready_when_call_session_store_health_check_raises() -> None:
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
        ),
        call_session_store=RaisingHealthCheckCallSessionStore(),
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "providers": {"stt": "local", "llm": "local", "tts": "local"},
        "public_base_url": "https://voice.example.com",
        "checks": {
            "call_event_store": "ok",
            "call_session_store": "unhealthy",
            "per_call_config_store": "ok",
            "accepting_calls": "yes",
        },
    }


def test_preview_tts_route_uses_agent_tts_provider() -> None:
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

    response = client.post(
        "/preview/tts",
        json={"text": "Namaste, this is a production voice preview.", "provider": "local"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["engine"] == "local"
    assert body["mime"] == "audio/wav"
    assert body["source_mime"] == "audio/L16;rate=16000"
    preview_audio = base64.b64decode(body["audio_base64"])
    assert preview_audio.startswith(b"RIFF")
    with wave.open(io.BytesIO(preview_audio), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 16_000
        assert wav_file.readframes(wav_file.getnframes())


@pytest.mark.anyio
async def test_readyz_times_out_hanging_dependency_health_check() -> None:
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            readiness_check_timeout_seconds=0.01,
        ),
        call_event_store=HangingHealthCheckCallEventStore(),
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await asyncio.wait_for(client.get("/readyz"), timeout=0.2)

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "providers": {"stt": "local", "llm": "local", "tts": "local"},
        "public_base_url": "https://voice.example.com",
        "checks": {
            "call_event_store": "unhealthy",
            "call_session_store": "ok",
            "per_call_config_store": "ok",
            "accepting_calls": "yes",
        },
    }


def test_readyz_reports_unready_when_per_call_config_store_is_unhealthy() -> None:
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
        ),
    )
    # A Redis-backed per-call config store that cannot be reached makes this replica
    # unable to resolve per-call overrides -> not ready.
    app.state.per_call_config_store = FailingPerCallConfigStore()
    client = TestClient(app)

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "providers": {"stt": "local", "llm": "local", "tts": "local"},
        "public_base_url": "https://voice.example.com",
        "checks": {
            "call_event_store": "ok",
            "call_session_store": "ok",
            "per_call_config_store": "unhealthy",
            "accepting_calls": "yes",
        },
    }


def test_latency_summary_route_returns_recent_voice_turn_bottleneck() -> None:
    event_store = InMemoryCallEventStore()
    event_store.events.extend(
        [
            {"event": "voice_turn_latency", "turn_committed_to_llm_first_chunk_ms": 900},
            {"event": "voice_turn_latency", "turn_committed_to_llm_first_chunk_ms": 1500},
        ]
    )
    app = create_app(
        _production_settings(),
        call_event_store=event_store,
        call_session_store=InMemoryCallSessionStore(),
    )
    client = TestClient(app, base_url="https://voice.example.com")

    response = client.get(
        "/latency/summary?limit=10",
        headers={"Authorization": "Bearer control-secret-0123456789abcdef012345"},
    )

    assert response.status_code == 200
    assert response.json()["turn_count"] == 2
    assert response.json()["bottleneck"] == {
        "stage": "llm_first_chunk",
        "label": "LLM first chunk",
        "p50_ms": 1200.0,
        "field": "turn_committed_to_llm_first_chunk_ms",
    }


def test_latency_summary_route_filters_by_call_id() -> None:
    event_store = InMemoryCallEventStore()
    event_store.events.extend(
        [
            {
                "event": "voice_turn_latency",
                "call_id": "call-1",
                "turn_committed_to_llm_first_chunk_ms": 100,
            },
            {
                "event": "voice_turn_latency",
                "call_id": "call-2",
                "turn_committed_to_llm_first_chunk_ms": 900,
            },
        ]
    )
    app = create_app(
        _production_settings(),
        call_event_store=event_store,
        call_session_store=InMemoryCallSessionStore(),
    )
    client = TestClient(app, base_url="https://voice.example.com")

    response = client.get(
        "/latency/summary?limit=10&call_id=call-1",
        headers={"Authorization": "Bearer control-secret-0123456789abcdef012345"},
    )

    assert response.status_code == 200
    assert response.json()["turn_count"] == 1
    assert response.json()["stages"]["llm_first_chunk"]["p50_ms"] == 100.0


def test_latency_summary_route_requires_control_auth_in_production() -> None:
    app = create_app(
        _production_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
    )
    client = TestClient(app, base_url="https://voice.example.com")

    response = client.get("/latency/summary")

    assert response.status_code == 401


def test_app_shutdown_closes_call_event_store() -> None:
    store = ClosableCallEventStore()
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
        ),
        call_event_store=store,
    )

    with TestClient(app):
        pass

    assert store.closed


def test_http_responses_include_observability_headers() -> None:
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

    response = client.get("/healthz", headers={"x-request-id": "req-123"})

    assert response.headers["x-request-id"] == "req-123"
    assert float(response.headers["x-process-time-ms"]) >= 0.0


@pytest.mark.parametrize("request_id", ["x" * 129, "req 123"])
def test_http_responses_replace_unsafe_request_id_header(request_id: str) -> None:
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

    response = client.get("/healthz", headers={"x-request-id": request_id})

    response_request_id = response.headers["x-request-id"]
    assert response_request_id != request_id
    assert re.fullmatch(r"[0-9a-f]{32}", response_request_id)


def test_production_hides_interactive_api_documentation_surface() -> None:
    app = create_app(_production_settings())
    client = TestClient(app)

    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_plivo_status_callback_is_recorded_for_audit() -> None:
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

    response = client.post(
        "/plivo/status",
        json={"CallUUID": "call-1", "CallStatus": "completed"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    assert app.state.call_event_store.events == [{"CallUUID": "call-1", "CallStatus": "completed"}]


def test_plivo_status_callback_returns_503_when_event_store_record_fails() -> None:
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
        call_session_store=InMemoryCallSessionStore(),
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/plivo/status",
        json={"CallUUID": "call-1", "CallStatus": "completed"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Call event storage is unavailable."
    assert client.get("/calls/call-1").status_code == 404


def test_plivo_status_callback_returns_503_when_session_update_fails() -> None:
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
        call_session_store=FailingUpsertCallSessionStore(),
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/plivo/status",
        json={"CallUUID": "call-1", "CallStatus": "completed"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Call session storage is unavailable."
    assert event_store.events == [{"CallUUID": "call-1", "CallStatus": "completed"}]


def test_plivo_status_callback_rejects_malformed_json_payload() -> None:
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
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/plivo/status",
        content=b'{"CallUUID":',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Callback payload must be valid JSON."
    assert event_store.events == []


def test_plivo_status_callback_rejects_non_utf8_json_payload() -> None:
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
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/plivo/status",
        content=b"\xff\xfe",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Callback payload must be valid UTF-8 JSON."
    assert event_store.events == []


def test_plivo_status_callback_rejects_non_object_json_payload() -> None:
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
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/plivo/status",
        json=["not", "a", "callback"],
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Callback payload must be a JSON object."
    assert event_store.events == []


def test_plivo_status_callback_rejects_duplicate_form_fields_before_recording() -> None:
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
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/plivo/status",
        content=b"CallUUID=call-1&CallUUID=call-2&CallStatus=completed",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Callback payload fields must be unique."
    assert event_store.events == []


def test_plivo_status_callback_rejects_duplicate_get_query_fields_before_recording() -> None:
    event_store = InMemoryCallEventStore()
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            stream_status_callback_method="GET",
        ),
        call_event_store=event_store,
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/plivo/status?CallUUID=call-1&CallUUID=call-2")

    assert response.status_code == 400
    assert response.json()["detail"] == "Callback payload fields must be unique."
    assert event_store.events == []


def test_plivo_status_callback_rejects_duplicate_json_fields_before_recording() -> None:
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
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/plivo/status",
        content=b'{"CallUUID":"call-1","CallUUID":"call-2","CallStatus":"completed"}',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Callback payload fields must be unique."
    assert event_store.events == []


def test_plivo_status_callback_rejects_blank_form_field_names_before_recording() -> None:
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
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/plivo/status",
        content=b"=shadow&CallUUID=call-1&CallStatus=completed",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Callback payload field names must not be blank."
    assert event_store.events == []


def test_plivo_status_callback_rejects_blank_get_query_field_names_before_recording() -> None:
    event_store = InMemoryCallEventStore()
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            stream_status_callback_method="GET",
        ),
        call_event_store=event_store,
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/plivo/status?=shadow&CallUUID=call-1")

    assert response.status_code == 400
    assert response.json()["detail"] == "Callback payload field names must not be blank."
    assert event_store.events == []


def test_plivo_status_callback_rejects_blank_json_field_names_before_recording() -> None:
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
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/plivo/status",
        content=b'{"":"shadow","CallUUID":"call-1","CallStatus":"completed"}',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Callback payload field names must not be blank."
    assert event_store.events == []


def test_plivo_status_callback_rejects_unsafe_json_field_names_before_recording() -> None:
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
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/plivo/status",
        content=b'{"Call\\nUUID":"call-1","CallStatus":"completed"}',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Callback payload field names contain unsafe characters."
    assert event_store.events == []


def test_plivo_status_callback_rejects_non_string_json_field_values_before_recording() -> None:
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
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/plivo/status",
        content=b'{"CallUUID":"call-1","CallStatus":["completed"]}',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Callback payload field values must be strings."
    assert event_store.events == []


def test_plivo_status_callback_rejects_oversized_json_payload_before_recording() -> None:
    event_store = InMemoryCallEventStore()
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            callback_max_body_bytes=16,
        ),
        call_event_store=event_store,
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/plivo/status",
        content=b'{"CallUUID":"call-1","CallStatus":"completed"}',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "Callback payload exceeds configured size limit."
    assert event_store.events == []


def test_plivo_answer_callback_rejects_oversized_form_payload_before_recording() -> None:
    event_store = InMemoryCallEventStore()
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            callback_max_body_bytes=16,
        ),
        call_event_store=event_store,
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/plivo/answer",
        content=b"CallUUID=call-1&Event=StartApp",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "Callback payload exceeds configured size limit."
    assert event_store.events == []


def test_plivo_status_callback_rejects_oversized_get_query_before_recording() -> None:
    event_store = InMemoryCallEventStore()
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            stream_status_callback_method="GET",
            callback_max_body_bytes=16,
        ),
        call_event_store=event_store,
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/plivo/status?CallUUID=call-1&CallStatus=completed")

    assert response.status_code == 413
    assert response.json()["detail"] == "Callback payload exceeds configured size limit."
    assert event_store.events == []


@pytest.mark.anyio
async def test_callback_payload_reader_stops_streaming_body_after_size_limit() -> None:
    request = StreamingCallbackRequest(
        chunks=[
            b'{"CallUUID":"',
            b"call-1",
            b'","CallStatus":"completed"}',
        ],
        content_type="application/json",
    )

    with pytest.raises(HTTPException) as exc_info:
        await _read_callback_payload(cast(Any, request), max_body_bytes=16)

    assert exc_info.value.status_code == 413
    assert request.body_called is False
    assert request.chunks_read < len(request.chunks)


@pytest.mark.anyio
async def test_callback_payload_reader_requires_exact_json_media_type() -> None:
    request = StreamingCallbackRequest(
        chunks=[b"CallUUID=call-1&CallStatus=completed"],
        content_type="application/not-application/json",
    )

    payload = await _read_callback_payload(cast(Any, request), max_body_bytes=4096)

    assert payload == {"CallUUID": "call-1", "CallStatus": "completed"}


@pytest.mark.anyio
async def test_callback_payload_reader_accepts_json_media_type_case_and_parameters() -> None:
    request = StreamingCallbackRequest(
        chunks=[b'{"CallUUID":"call-1","CallStatus":"completed"}'],
        content_type="Application/JSON; charset=utf-8",
    )

    payload = await _read_callback_payload(cast(Any, request), max_body_bytes=4096)

    assert payload == {"CallUUID": "call-1", "CallStatus": "completed"}


def test_production_plivo_status_callback_rejects_missing_signature() -> None:
    app = create_app(_production_settings())
    client = TestClient(app)

    response = client.post(
        "/plivo/status",
        data={"CallUUID": "call-1", "CallStatus": "completed"},
    )

    assert response.status_code == 403


def test_production_plivo_stream_status_callback_acknowledged_without_signature() -> None:
    # Plivo's media-STREAM status callback (carries StreamID) signs in a form that can't be
    # reconstructed; it must be acknowledged (200) — never 403, which would abort the live
    # call. The unverified event is not recorded.
    app = create_app(_production_settings())
    client = TestClient(app)

    response = client.post(
        "/plivo/status",
        data={"CallUUID": "call-1", "StreamID": "stream-1", "Event": "StartedStreaming"},
    )

    assert response.status_code == 200


def test_production_plivo_answer_callback_rejects_missing_signature() -> None:
    app = create_app(_production_settings())
    client = TestClient(app)

    response = client.post("/plivo/answer")

    assert response.status_code == 403


def test_production_plivo_answer_callback_rejects_missing_call_identifier() -> None:
    settings = _production_settings()
    app = create_app(
        settings,
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
    )
    client = TestClient(app, base_url="https://voice.example.com")
    nonce = "nonce-123"
    signature = build_v3_signature(
        method="POST",
        url="https://voice.example.com/plivo/answer",
        nonce=nonce,
        auth_token="plivo-auth-token",
    )

    response = client.post(
        "/plivo/answer",
        headers={
            "X-Plivo-Signature-V3": signature,
            "X-Plivo-Signature-V3-Nonce": nonce,
        },
    )

    assert response.status_code == 400


def test_production_plivo_answer_callback_rejects_blank_call_identifier() -> None:
    settings = _production_settings()
    event_store = InMemoryCallEventStore()
    app = create_app(
        settings,
        call_event_store=event_store,
        call_session_store=InMemoryCallSessionStore(),
    )
    client = TestClient(app, base_url="https://voice.example.com")
    form = {"CallUUID": "   "}
    nonce = "nonce-123"
    signature = build_v3_signature(
        method="POST",
        url="https://voice.example.com/plivo/answer",
        nonce=nonce,
        auth_token="plivo-auth-token",
        params=form,
    )

    response = client.post(
        "/plivo/answer",
        data=form,
        headers={
            "X-Plivo-Signature-V3": signature,
            "X-Plivo-Signature-V3-Nonce": nonce,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Plivo callback call identifier must not be blank."
    assert event_store.events == []


def test_plivo_status_callback_rejects_blank_call_identifier_before_audit() -> None:
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
        call_session_store=InMemoryCallSessionStore(),
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/plivo/status",
        json={"CallUUID": "   ", "CallStatus": "completed"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Plivo callback call identifier must not be blank."
    assert event_store.events == []


def test_plivo_status_callback_rejects_conflicting_call_identifier_aliases_before_audit() -> None:
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
        call_session_store=InMemoryCallSessionStore(),
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/plivo/status",
        json={
            "CallUUID": "call-1",
            "CallUuid": "call-2",
            "CallStatus": "completed",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Plivo callback call identifier aliases conflict."
    assert event_store.events == []


def test_plivo_status_callback_rejects_conflicting_request_identifier_aliases() -> None:
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
        call_session_store=InMemoryCallSessionStore(),
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/plivo/status",
        json={
            "RequestUUID": "request-1",
            "requestUuid": "request-2",
            "CallStatus": "completed",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Plivo callback request identifier aliases conflict."
    assert event_store.events == []


def test_plivo_status_callback_rejects_conflicting_from_number_aliases_before_audit() -> None:
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
        call_session_store=InMemoryCallSessionStore(),
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/plivo/status",
        json={
            "CallUUID": "call-1",
            "From": "+14155550100",
            "from": "+14155550101",
            "CallStatus": "completed",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Plivo callback from number aliases conflict."
    assert event_store.events == []


def test_plivo_status_callback_rejects_conflicting_status_aliases_before_audit() -> None:
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
        call_session_store=InMemoryCallSessionStore(),
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/plivo/status",
        json={
            "CallUUID": "call-1",
            "CallStatus": "completed",
            "call_status": "busy",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Plivo callback status aliases conflict."
    assert event_store.events == []


def test_plivo_status_callback_rejects_unsafe_status_value_before_audit() -> None:
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
        call_session_store=InMemoryCallSessionStore(),
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/plivo/status",
        json={
            "CallUUID": "call-1",
            "CallStatus": "completed\ninjected",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Plivo callback status value must be 1-64 characters using letters, numbers, "
        "dot, underscore, colon, or hyphen."
    )
    assert event_store.events == []


def test_plivo_answer_callback_rejects_unsafe_event_value_before_audit() -> None:
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
        call_session_store=InMemoryCallSessionStore(),
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/plivo/answer",
        json={
            "CallUUID": "call-1",
            "Event": "Start\nApp",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Plivo callback event value must be 1-64 characters using letters, numbers, "
        "dot, underscore, colon, or hyphen."
    )
    assert event_store.events == []


def test_plivo_status_callback_rejects_unsafe_direction_value_before_audit() -> None:
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
        call_session_store=InMemoryCallSessionStore(),
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/plivo/status",
        json={
            "CallUUID": "call-1",
            "Direction": "outbound\nshadow",
            "CallStatus": "completed",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Plivo callback direction value must be 1-64 characters using letters, numbers, "
        "dot, underscore, colon, or hyphen."
    )
    assert event_store.events == []


def test_plivo_status_callback_rejects_unsafe_from_number_value_before_audit() -> None:
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
        call_session_store=InMemoryCallSessionStore(),
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/plivo/status",
        json={
            "CallUUID": "call-1",
            "From": "+14155550100\nshadow",
            "CallStatus": "completed",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Plivo callback from number value must be 1-128 characters using safe endpoint characters."
    )
    assert event_store.events == []


def test_production_plivo_status_callback_rejects_unsafe_call_identifier_before_audit() -> None:
    settings = _production_settings()
    event_store = InMemoryCallEventStore()
    app = create_app(
        settings,
        call_event_store=event_store,
        call_session_store=InMemoryCallSessionStore(),
    )
    client = TestClient(app, base_url="https://voice.example.com")
    form = {"CallUUID": "call/1", "CallStatus": "completed"}
    nonce = "nonce-123"
    signature = build_v3_signature(
        method="POST",
        url="https://voice.example.com/plivo/status",
        nonce=nonce,
        auth_token="plivo-auth-token",
        params=form,
    )

    response = client.post(
        "/plivo/status",
        data=form,
        headers={
            "X-Plivo-Signature-V3": signature,
            "X-Plivo-Signature-V3-Nonce": nonce,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Plivo callback call identifier must be 1-128 characters using letters, "
        "numbers, dot, underscore, colon, or hyphen."
    )
    assert event_store.events == []


def test_production_plivo_status_callback_accepts_valid_v3_signature() -> None:
    settings = _production_settings()
    event_store = InMemoryCallEventStore()
    app = create_app(
        settings,
        call_event_store=event_store,
        call_session_store=InMemoryCallSessionStore(),
    )
    client = TestClient(app, base_url="https://voice.example.com")
    form = {"CallUUID": "call-1", "CallStatus": "completed"}
    nonce = "nonce-123"
    signature = build_v3_signature(
        method="POST",
        url="https://voice.example.com/plivo/status",
        nonce=nonce,
        auth_token="plivo-auth-token",
        params=form,
    )

    response = client.post(
        "/plivo/status",
        data=form,
        headers={
            "X-Plivo-Signature-V3": signature,
            "X-Plivo-Signature-V3-Nonce": nonce,
        },
    )

    assert response.status_code == 200
    assert event_store.events == [form]


def test_production_plivo_status_callback_accepts_signed_get_when_configured() -> None:
    settings = _production_settings(stream_status_callback_method="GET")
    event_store = InMemoryCallEventStore()
    app = create_app(
        settings,
        call_event_store=event_store,
        call_session_store=InMemoryCallSessionStore(),
    )
    client = TestClient(app, base_url="https://voice.example.com")
    query = "CallUUID=call-1&CallStatus=completed"
    nonce = "nonce-123"
    signature = build_v3_signature(
        method="GET",
        url=f"https://voice.example.com/plivo/status?{query}",
        nonce=nonce,
        auth_token="plivo-auth-token",
    )

    response = client.get(
        f"/plivo/status?{query}",
        headers={
            "X-Plivo-Signature-V3": signature,
            "X-Plivo-Signature-V3-Nonce": nonce,
        },
    )

    assert response.status_code == 200
    assert event_store.events == [{"CallUUID": "call-1", "CallStatus": "completed"}]


def test_production_plivo_status_callback_rejects_get_when_post_configured() -> None:
    settings = _production_settings()
    event_store = InMemoryCallEventStore()
    app = create_app(
        settings,
        call_event_store=event_store,
        call_session_store=InMemoryCallSessionStore(),
    )
    client = TestClient(app, base_url="https://voice.example.com")
    query = "CallUUID=call-1&CallStatus=completed"
    nonce = "nonce-123"
    signature = build_v3_signature(
        method="GET",
        url=f"https://voice.example.com/plivo/status?{query}",
        nonce=nonce,
        auth_token="plivo-auth-token",
    )

    response = client.get(
        f"/plivo/status?{query}",
        headers={
            "X-Plivo-Signature-V3": signature,
            "X-Plivo-Signature-V3-Nonce": nonce,
        },
    )

    assert response.status_code == 405
    assert response.json()["detail"] == "Plivo status callback method must be POST."
    assert event_store.events == []
    assert (
        client.get(
            "/calls/call-1",
            headers={"Authorization": "Bearer control-secret-0123456789abcdef012345"},
        ).status_code
        == 404
    )


def test_production_plivo_status_callback_rejects_post_when_get_configured() -> None:
    settings = _production_settings(stream_status_callback_method="GET")
    event_store = InMemoryCallEventStore()
    app = create_app(
        settings,
        call_event_store=event_store,
        call_session_store=InMemoryCallSessionStore(),
    )
    client = TestClient(app, base_url="https://voice.example.com")
    form = {"CallUUID": "call-1", "CallStatus": "completed"}
    nonce = "nonce-123"
    signature = build_v3_signature(
        method="POST",
        url="https://voice.example.com/plivo/status",
        nonce=nonce,
        auth_token="plivo-auth-token",
        params=form,
    )

    response = client.post(
        "/plivo/status",
        data=form,
        headers={
            "X-Plivo-Signature-V3": signature,
            "X-Plivo-Signature-V3-Nonce": nonce,
        },
    )

    assert response.status_code == 405
    assert response.json()["detail"] == "Plivo status callback method must be GET."
    assert event_store.events == []
    assert (
        client.get(
            "/calls/call-1",
            headers={"Authorization": "Bearer control-secret-0123456789abcdef012345"},
        ).status_code
        == 404
    )


def test_plivo_status_callback_rejects_wrong_method_before_body_parsing() -> None:
    settings = _production_settings(stream_status_callback_method="GET")
    event_store = InMemoryCallEventStore()
    app = create_app(
        settings,
        call_event_store=event_store,
        call_session_store=InMemoryCallSessionStore(),
    )
    client = TestClient(app, base_url="https://voice.example.com", raise_server_exceptions=False)

    response = client.post(
        "/plivo/status",
        content=b'{"CallUUID":',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 405
    assert response.json()["detail"] == "Plivo status callback method must be GET."
    assert event_store.events == []


def test_production_plivo_answer_callback_accepts_valid_v3_signature() -> None:
    settings = _production_settings()
    app = create_app(
        settings,
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
    )
    client = TestClient(app, base_url="https://voice.example.com")
    form = {"CallUUID": "call-1"}
    nonce = "nonce-123"
    signature = build_v3_signature(
        method="POST",
        url="https://voice.example.com/plivo/answer",
        nonce=nonce,
        auth_token="plivo-auth-token",
        params=form,
    )

    response = client.post(
        "/plivo/answer",
        data=form,
        headers={
            "X-Plivo-Signature-V3": signature,
            "X-Plivo-Signature-V3-Nonce": nonce,
        },
    )

    assert response.status_code == 200
    assert "wss://voice.example.com/plivo/media?token=" in response.text
    assert "stream-secret-0123456789abcdef012345" not in response.text


def test_production_outbound_call_route_rejects_missing_control_auth() -> None:
    app = create_app(
        _production_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
        outbound_call_client=FakeOutboundCallClient(),
    )
    client = TestClient(app)

    response = client.post("/calls/outbound", json={"to": "+919876543210"})

    assert response.status_code == 401
    assert app.state.outbound_call_client.calls == []


def test_outbound_call_route_rejects_missing_control_auth_before_body_validation() -> None:
    app = create_app(
        _production_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
        outbound_call_client=FakeOutboundCallClient(),
    )
    client = TestClient(app)

    response = client.post(
        "/calls/outbound",
        content=b'{"to":',
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": "workflow-call-1",
        },
    )

    assert response.status_code == 401
    assert app.state.outbound_call_client.calls == []


def test_production_outbound_call_route_starts_plivo_call_with_answer_url() -> None:
    app = create_app(
        _production_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
        outbound_call_client=FakeOutboundCallClient(),
    )
    client = TestClient(app)

    response = client.post(
        "/calls/outbound",
        headers={
            "Authorization": "Bearer control-secret-0123456789abcdef012345",
            "Idempotency-Key": "workflow-call-1",
        },
        json={"to": "+919876543210"},
    )

    assert response.status_code == 202
    assert response.json() == {
        "api_id": "api-1",
        "message": "call fired",
        "request_uuid": "request-1",
    }
    assert app.state.outbound_call_client.calls == [
        {
            "from_number": "+14155550100",
            "to_number": "+919876543210",
            "answer_url": "https://voice.example.com/plivo/answer",
            "answer_method": "POST",
            "hangup_url": "https://voice.example.com/plivo/status",
            "hangup_method": "POST",
            "hangup_on_ring_seconds": 45,
            "time_limit_seconds": 900,
        }
    ]


def test_production_outbound_call_route_schedules_first_message_prewarm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prewarmed: list[Any] = []

    def fake_schedule_prewarm(**kwargs: Any) -> None:
        prewarmed.append(kwargs["per_call_config"])

    monkeypatch.setattr(
        "voice_agent.app._schedule_outbound_greeting_prewarm",
        fake_schedule_prewarm,
    )
    app = create_app(
        _production_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
        outbound_call_client=FakeOutboundCallClient(),
    )
    app.state.per_call_config_store = InMemoryPerCallConfigStore()
    client = TestClient(app)

    response = client.post(
        "/calls/outbound",
        headers={
            "Authorization": "Bearer control-secret-0123456789abcdef012345",
            "Idempotency-Key": "workflow-call-preset-greeting",
        },
        json={
            "to": "+919876543210",
            "first_message": "Hi, this is VoicePilot. I am ready to help.",
            "tts": {
                "provider": "elevenlabs",
                "voice": "voice-1",
                "model": "eleven_flash_v2_5",
            },
        },
    )

    assert response.status_code == 202
    assert len(prewarmed) == 1
    assert prewarmed[0].greeting == "Hi, this is VoicePilot. I am ready to help."
    assert prewarmed[0].tts == {
        "provider": "elevenlabs",
        "voice": "voice-1",
        "model": "eleven_flash_v2_5",
    }


def test_production_outbound_call_route_uses_configured_max_call_duration() -> None:
    app = create_app(
        _production_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
        outbound_call_client=FakeOutboundCallClient(),
    )
    app.state.per_call_config_store = InMemoryPerCallConfigStore()
    client = TestClient(app)

    response = client.post(
        "/calls/outbound",
        headers={
            "Authorization": "Bearer control-secret-0123456789abcdef012345",
            "Idempotency-Key": "workflow-call-1",
        },
        json={
            "to": "+919876543210",
            "behavior": {"max_call_duration_seconds": 1800},
        },
    )

    assert response.status_code == 202
    assert app.state.outbound_call_client.calls[0]["time_limit_seconds"] == 1800


def test_outbound_dry_run_simulates_call_without_plivo() -> None:
    # Dev/test dry-run: records the call + returns 202, with NO real Plivo client used.
    settings = Settings(
        environment="test",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="local",
        tts_provider="local",
        plivo_from_number="+14155550100",
        control_api_key="control-secret-0123456789abcdef012345",
        outbound_dry_run=True,
    )
    app = create_app(
        settings,
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
        outbound_call_client=None,
    )
    client = TestClient(app)

    response = client.post(
        "/calls/outbound",
        headers={
            "Authorization": "Bearer control-secret-0123456789abcdef012345",
            "Idempotency-Key": "dry-run-call-001",
        },
        json={"to": "+919876543210"},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["dry_run"] is True
    assert body["request_uuid"].startswith("dryrun-")
    assert app.state.outbound_call_client is None


def test_preview_turn_runs_llm_without_creating_call() -> None:
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            control_api_key="control-secret-0123456789abcdef012345",
        ),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
        outbound_call_client=FakeOutboundCallClient(),
    )
    client = TestClient(app)

    response = client.post(
        "/preview/turn",
        headers={"Authorization": "Bearer control-secret-0123456789abcdef012345"},
        json={
            "user_text": "I want to book an appointment.",
            "system_prompt": "You are a receptionist.",
            "behavior": {"closing_message": "Thanks for calling. Goodbye."},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "user_text": "I want to book an appointment.",
        "reply": "I can help with that.",
        "end_call": False,
        "closing_message": None,
    }
    assert app.state.outbound_call_client.calls == []


def test_outbound_dry_run_is_force_disabled_in_production() -> None:
    # Even if dry-run is flipped on in production, the real call must still be placed.
    app = create_app(
        _production_settings(outbound_dry_run=True),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
        outbound_call_client=FakeOutboundCallClient(),
    )
    client = TestClient(app)

    response = client.post(
        "/calls/outbound",
        headers={
            "Authorization": "Bearer control-secret-0123456789abcdef012345",
            "Idempotency-Key": "prod-real-1",
        },
        json={"to": "+919876543210"},
    )

    assert response.status_code == 202
    assert response.json() == {
        "api_id": "api-1",
        "message": "call fired",
        "request_uuid": "request-1",
    }
    # The real Plivo client WAS invoked — production never simulates.
    assert len(app.state.outbound_call_client.calls) == 1


def test_production_outbound_call_route_uses_configured_status_callback_method() -> None:
    app = create_app(
        _production_settings(stream_status_callback_method="GET"),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
        outbound_call_client=FakeOutboundCallClient(),
    )
    client = TestClient(app)

    response = client.post(
        "/calls/outbound",
        headers={
            "Authorization": "Bearer control-secret-0123456789abcdef012345",
            "Idempotency-Key": "workflow-call-1",
        },
        json={"to": "+919876543210"},
    )

    assert response.status_code == 202
    assert app.state.outbound_call_client.calls[0]["hangup_method"] == "GET"


def test_outbound_call_route_rejects_oversized_json_payload_before_session_reservation() -> None:
    app = create_app(
        _production_settings(control_max_body_bytes=16),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=FailingReserveCallSessionStore(),
        outbound_call_client=FakeOutboundCallClient(),
    )
    client = TestClient(app)

    response = client.post(
        "/calls/outbound",
        content=b'{"to":"+919876543210","padding":"too-large"}',
        headers={
            "Authorization": "Bearer control-secret-0123456789abcdef012345",
            "Content-Type": "application/json",
            "Idempotency-Key": "workflow-call-1",
        },
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "Control request payload exceeds configured size limit."
    assert app.state.outbound_call_client.calls == []


def test_control_auth_rejects_non_ascii_authorization_header() -> None:
    request = HeaderOnlyRequest({"Authorization": "Bearer control-secret-é"})

    with pytest.raises(HTTPException) as exc_info:
        _validate_control_request(cast(Any, request), _production_settings())

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid control API credentials."


def test_control_content_length_rejects_negative_value() -> None:
    request = HeaderOnlyRequest({"content-length": "-1"})

    with pytest.raises(HTTPException) as exc_info:
        _reject_oversized_control_content_length(cast(Any, request), max_body_bytes=4096)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Content-Length header must be non-negative."


def test_callback_content_length_rejects_negative_value() -> None:
    request = HeaderOnlyRequest({"content-length": "-1"})

    with pytest.raises(HTTPException) as exc_info:
        _reject_oversized_callback_content_length(cast(Any, request), max_body_bytes=4096)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Content-Length header must be non-negative."


def test_production_call_lookup_returns_503_when_session_store_get_fails() -> None:
    app = create_app(
        _production_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=FailingGetCallSessionStore(),
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(
        "/calls/call-1",
        headers={"Authorization": "Bearer control-secret-0123456789abcdef012345"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Call session storage is unavailable."


def test_production_hangup_returns_503_when_session_store_get_fails() -> None:
    call_client = FakeOutboundCallClient()
    app = create_app(
        _production_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=FailingGetCallSessionStore(),
        outbound_call_client=call_client,
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.delete(
        "/calls/call-1",
        headers={"Authorization": "Bearer control-secret-0123456789abcdef012345"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Call session storage is unavailable."
    assert call_client.hangups == []


def test_production_hangup_returns_503_when_session_update_fails_after_plivo_accepts() -> None:
    call_client = FakeOutboundCallClient()
    app = create_app(
        _production_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=FailingUpsertAfterGetCallSessionStore(),
        outbound_call_client=call_client,
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.delete(
        "/calls/call-1",
        headers={"Authorization": "Bearer control-secret-0123456789abcdef012345"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Call session storage is unavailable."
    assert call_client.hangups == ["request-1"]


def test_hangup_route_preserves_plivo_error_when_failed_marker_update_fails() -> None:
    call_client = InvalidHangupOutboundCallClient()
    app = create_app(
        _production_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=FailingUpsertAfterGetCallSessionStore(),
        outbound_call_client=call_client,
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.delete(
        "/calls/call-1",
        headers={"Authorization": "Bearer control-secret-0123456789abcdef012345"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "Plivo returned an invalid hangup response."
    assert call_client.hangups == ["request-1"]


def test_outbound_call_route_returns_503_when_session_reservation_fails() -> None:
    call_client = FakeOutboundCallClient()
    app = create_app(
        _production_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=FailingReserveCallSessionStore(),
        outbound_call_client=call_client,
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/calls/outbound",
        headers={
            "Authorization": "Bearer control-secret-0123456789abcdef012345",
            "Idempotency-Key": "workflow-call-1",
        },
        json={"to": "+919876543210"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Call session storage is unavailable."
    assert call_client.calls == []


def test_outbound_call_route_hangs_up_when_session_persistence_fails_after_plivo_accepts() -> None:
    call_client = FakeOutboundCallClient()
    app = create_app(
        _production_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=FailingUpsertAfterReserveCallSessionStore(),
        outbound_call_client=call_client,
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/calls/outbound",
        headers={
            "Authorization": "Bearer control-secret-0123456789abcdef012345",
            "Idempotency-Key": "workflow-call-1",
        },
        json={"to": "+919876543210"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Call session storage is unavailable."
    assert len(call_client.calls) == 1
    assert call_client.hangups == ["request-1"]


def test_outbound_call_route_preserves_plivo_error_when_failed_marker_update_fails() -> None:
    call_client = InvalidCreateOutboundCallClient()
    app = create_app(
        _production_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=FailingUpsertAfterReserveCallSessionStore(),
        outbound_call_client=call_client,
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/calls/outbound",
        headers={
            "Authorization": "Bearer control-secret-0123456789abcdef012345",
            "Idempotency-Key": "workflow-call-1",
        },
        json={"to": "+919876543210"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "Plivo returned an invalid outbound call response."
    assert len(call_client.calls) == 1


def test_outbound_call_route_surfaces_plivo_rejection_reason() -> None:
    call_client = RejectedCreateOutboundCallClient(
        status_code=400,
        body={"api_id": "api-1", "error": "answer_url parameter is not valid"},
    )
    app = create_app(
        _production_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
        outbound_call_client=call_client,
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/calls/outbound",
        headers={
            "Authorization": "Bearer control-secret-0123456789abcdef012345",
            "Idempotency-Key": "workflow-call-1",
        },
        json={"to": "+919876543210"},
    )

    assert response.status_code == 502
    assert (
        response.json()["detail"]
        == "Plivo rejected the outbound call request: answer_url parameter is not valid."
    )
    assert len(call_client.calls) == 1


def test_outbound_call_route_rejects_unreachable_public_callback_before_plivo() -> None:
    call_client = FakeOutboundCallClient()
    app = create_app(
        _production_settings(public_base_url="https://expired-tunnel.trycloudflare.com"),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
        outbound_call_client=call_client,
    )

    async def callback_url_error(url: str) -> str | None:
        assert url == "https://expired-tunnel.trycloudflare.com/plivo/answer"
        return "Public callback host expired-tunnel.trycloudflare.com cannot be resolved."

    app.state.outbound_callback_url_resolver = callback_url_error
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/calls/outbound",
        headers={
            "Authorization": "Bearer control-secret-0123456789abcdef012345",
            "Idempotency-Key": "workflow-call-1",
        },
        json={"to": "+919876543210"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Public callback URL is not reachable: Public callback host "
        "expired-tunnel.trycloudflare.com cannot be resolved. Refresh "
        "VOICE_AGENT_PUBLIC_BASE_URL to the active HTTPS tunnel before placing outbound calls."
    )
    assert call_client.calls == []


async def test_public_callback_url_resolver_rejects_unhealthy_public_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_urls: list[str] = []

    class FakeAsyncClient:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def get(self, url: str) -> httpx.Response:
            requested_urls.append(url)
            return httpx.Response(
                502,
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr("voice_agent.app.httpx.AsyncClient", FakeAsyncClient)

    error = await _resolve_public_callback_url("https://localhost:8443/plivo/answer?cfg=abc")

    assert requested_urls == ["https://localhost:8443/healthz"]
    assert error == "Public callback health check https://localhost:8443/healthz returned HTTP 502."


def _production_settings(
    *,
    public_base_url: str = "https://voice.example.com",
    stream_status_callback_method: str = "POST",
    control_max_body_bytes: int = 4096,
    outbound_dry_run: bool = False,
) -> Settings:
    return Settings(
        environment="production",
        outbound_dry_run=outbound_dry_run,
        public_base_url=public_base_url,
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
        plivo_auth_token="plivo-auth-token",
        stream_auth_token="stream-secret-0123456789abcdef012345",
        plivo_auth_id="plivo-auth-id",
        plivo_from_number="+14155550100",
        control_api_key="control-secret-0123456789abcdef012345",
        control_max_body_bytes=control_max_body_bytes,
        outbound_hangup_on_ring_seconds=45,
        outbound_time_limit_seconds=900,
        stream_status_callback_method=stream_status_callback_method,
    )


class FailingCallEventStore:
    async def record(self, event: dict[str, Any]) -> None:
        del event

    async def recent(self, *, count: int) -> list[dict[str, Any]]:
        del count
        return []

    async def health_check(self) -> bool:
        return False

    async def close(self) -> None:
        return None


class RaisingHealthCheckCallEventStore:
    async def record(self, event: dict[str, Any]) -> None:
        del event

    async def recent(self, *, count: int) -> list[dict[str, Any]]:
        del count
        return []

    async def health_check(self) -> bool:
        raise RuntimeError("call event health check failed")

    async def close(self) -> None:
        return None


class HangingHealthCheckCallEventStore:
    async def record(self, event: dict[str, Any]) -> None:
        del event

    async def recent(self, *, count: int) -> list[dict[str, Any]]:
        del count
        return []

    async def health_check(self) -> bool:
        await asyncio.Event().wait()
        return True

    async def close(self) -> None:
        return None


class FailingPerCallConfigStore:
    async def health_check(self) -> bool:
        return False

    async def close(self) -> None:
        return None


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


class StreamingCallbackRequest:
    method = "POST"

    def __init__(self, *, chunks: list[bytes], content_type: str) -> None:
        self.chunks = chunks
        self.headers = {"content-type": content_type}
        self.chunks_read = 0
        self.body_called = False

    async def body(self) -> bytes:
        self.body_called = True
        body = b""
        async for chunk in self.stream():
            body += chunk
        return body

    async def stream(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.chunks_read += 1
            yield chunk


class HeaderOnlyRequest:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


class FailingUpsertCallSessionStore:
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


class RaisingHealthCheckCallSessionStore:
    async def upsert(self, update: Any) -> dict[str, Any]:
        del update
        return {}

    async def reserve_idempotency_key(self, update: Any) -> dict[str, Any] | None:
        del update
        return None

    async def get(self, identifier: str) -> dict[str, Any] | None:
        del identifier
        return None

    async def health_check(self) -> bool:
        raise RuntimeError("call session health check failed")

    async def close(self) -> None:
        return None


class FailingGetCallSessionStore:
    async def upsert(self, update: Any) -> dict[str, Any]:
        del update
        raise AssertionError("upsert should not run when get fails")

    async def reserve_idempotency_key(self, update: Any) -> dict[str, Any] | None:
        del update
        raise AssertionError("reserve should not run when get fails")

    async def get(self, identifier: str) -> dict[str, Any] | None:
        del identifier
        raise RuntimeError("call session storage is unavailable")

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


class FailingUpsertAfterGetCallSessionStore:
    async def upsert(self, update: Any) -> dict[str, Any]:
        del update
        raise RuntimeError("call session storage is unavailable")

    async def reserve_idempotency_key(self, update: Any) -> dict[str, Any] | None:
        del update
        raise AssertionError("reserve should not run during hangup")

    async def get(self, identifier: str) -> dict[str, Any] | None:
        del identifier
        return {
            "request_uuid": "request-1",
            "status": "in_progress",
        }

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


class FailingReserveCallSessionStore:
    async def upsert(self, update: Any) -> dict[str, Any]:
        del update
        raise AssertionError("upsert should not run after reservation failure")

    async def reserve_idempotency_key(self, update: Any) -> dict[str, Any] | None:
        del update
        raise RuntimeError("call session storage is unavailable")

    async def get(self, identifier: str) -> dict[str, Any] | None:
        del identifier
        return None

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


class FailingUpsertAfterReserveCallSessionStore:
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


class ClosableCallEventStore:
    def __init__(self) -> None:
        self.closed = False

    async def record(self, event: dict[str, Any]) -> None:
        del event

    async def recent(self, *, count: int) -> list[dict[str, Any]]:
        del count
        return []

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        self.closed = True


class FakeOutboundCallClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.hangups: list[str] = []

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
        self.calls.append(
            {
                "from_number": from_number,
                "to_number": to_number,
                "answer_url": answer_url,
                "answer_method": answer_method,
                "hangup_url": hangup_url,
                "hangup_method": hangup_method,
                "hangup_on_ring_seconds": hangup_on_ring_seconds,
                "time_limit_seconds": time_limit_seconds,
            }
        )
        return PlivoCallCreateResult(
            api_id="api-1",
            message="call fired",
            request_uuid="request-1",
        )

    async def hangup_call(self, identifier: str) -> None:
        self.hangups.append(identifier)


class InvalidHangupOutboundCallClient(FakeOutboundCallClient):
    async def hangup_call(self, identifier: str) -> None:
        self.hangups.append(identifier)
        raise PlivoCallClientError("Plivo hangup response was invalid.")


class InvalidCreateOutboundCallClient(FakeOutboundCallClient):
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
        await super().create_call(
            from_number=from_number,
            to_number=to_number,
            answer_url=answer_url,
            answer_method=answer_method,
            hangup_url=hangup_url,
            hangup_method=hangup_method,
            hangup_on_ring_seconds=hangup_on_ring_seconds,
            time_limit_seconds=time_limit_seconds,
        )
        raise PlivoCallClientError("Plivo create-call response was invalid.")


class RejectedCreateOutboundCallClient(FakeOutboundCallClient):
    def __init__(self, *, status_code: int, body: dict[str, Any]) -> None:
        super().__init__()
        self._status_code = status_code
        self._body = body

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
        await super().create_call(
            from_number=from_number,
            to_number=to_number,
            answer_url=answer_url,
            answer_method=answer_method,
            hangup_url=hangup_url,
            hangup_method=hangup_method,
            hangup_on_ring_seconds=hangup_on_ring_seconds,
            time_limit_seconds=time_limit_seconds,
        )
        request = httpx.Request("POST", "https://api.plivo.com/v1/Account/auth-id/Call/")
        response = httpx.Response(self._status_code, json=self._body, request=request)
        raise httpx.HTTPStatusError(
            "Plivo rejected outbound call request.",
            request=request,
            response=response,
        )


class RaisingPutPerCallConfigStore:
    """Per-call config store whose put() fails, simulating a Redis blip while preparing
    an outbound call after the idempotency key has already been reserved."""

    async def put(self, key: str, config: Any) -> None:
        del key, config
        raise RuntimeError("per-call config storage is unavailable")

    async def get(self, key: str) -> Any | None:
        del key
        return None

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


def test_outbound_call_marks_reservation_failed_when_per_call_config_storage_fails() -> None:
    # Regression: a per-call config put() failure used to leave the idempotency row orphaned
    # in "creating" for the full TTL, poisoning the key. It must finalize as "failed" instead.
    app = create_app(
        _production_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
        outbound_call_client=FakeOutboundCallClient(),
    )
    app.state.per_call_config_store = RaisingPutPerCallConfigStore()
    client = TestClient(app)
    headers = {
        "Authorization": "Bearer control-secret-0123456789abcdef012345",
        "Idempotency-Key": "cfg-store-down-001",
    }
    body = {"to": "+919876543210", "system_prompt": "You are a helpful assistant."}

    first = client.post("/calls/outbound", headers=headers, json=body)
    assert first.status_code == 503
    assert "configuration storage" in first.json()["detail"].lower()
    # No real Plivo call should have been placed (we failed before dialing).
    assert app.state.outbound_call_client.calls == []

    # Retrying with the SAME key must surface the "previously failed -> use a new key" 409,
    # proving the reservation was finalized as failed rather than orphaned in "creating".
    second = client.post("/calls/outbound", headers=headers, json=body)
    assert second.status_code == 409
    assert "failed" in second.json()["detail"].lower()


def test_readyz_reports_not_ready_while_shutting_down() -> None:
    # During graceful shutdown /readyz must flip to not_ready so the load balancer stops
    # routing new calls, even though every dependency is still healthy.
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
        ),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
    )
    app.state.shutting_down = True
    client = TestClient(app)

    response = client.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["accepting_calls"] == "no"
    assert body["checks"]["call_session_store"] == "ok"


def test_metrics_endpoint_exposes_prometheus_text() -> None:
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

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "voice_agent_active_media_sessions" in response.text
    assert "voice_agent_turns_total" in response.text


def test_preview_turn_is_rate_limited() -> None:
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            control_rate_limit_per_minute=2,
        )
    )
    client = TestClient(app)
    body = {"user_text": "hello"}

    assert client.post("/preview/turn", json=body).status_code == 200
    assert client.post("/preview/turn", json=body).status_code == 200
    third = client.post("/preview/turn", json=body)
    assert third.status_code == 429
    assert third.headers["Retry-After"] == "60"


def test_preview_turn_rate_limit_can_be_disabled() -> None:
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            rate_limit_enabled=False,
            control_rate_limit_per_minute=1,
        )
    )
    client = TestClient(app)
    body = {"user_text": "hello"}

    assert client.post("/preview/turn", json=body).status_code == 200
    assert client.post("/preview/turn", json=body).status_code == 200


def test_plivo_answer_rejects_replayed_webhook_nonce_when_enabled() -> None:
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            webhook_replay_protection=True,
        )
    )
    client = TestClient(app)
    body = {"CallUUID": "call-1", "Event": "answer"}

    first = client.post(
        "/plivo/answer", json=body, headers={"X-Plivo-Signature-V3-Nonce": "nonce-1"}
    )
    assert first.status_code == 200

    replay = client.post(
        "/plivo/answer", json=body, headers={"X-Plivo-Signature-V3-Nonce": "nonce-1"}
    )
    assert replay.status_code == 409

    # A different nonce is accepted.
    fresh = client.post(
        "/plivo/answer", json=body, headers={"X-Plivo-Signature-V3-Nonce": "nonce-2"}
    )
    assert fresh.status_code == 200


def test_plivo_status_rejects_unverified_stream_callback_when_enforced() -> None:
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            plivo_auth_token="plivo-auth-token",
            enforce_stream_status_signature=True,
        )
    )
    client = TestClient(app)

    response = client.post("/plivo/status", json={"StreamID": "stream-1", "Event": "stream"})

    assert response.status_code == 403


def test_plivo_status_accepts_unverified_stream_callback_by_default() -> None:
    app = create_app(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            plivo_auth_token="plivo-auth-token",
        )
    )
    client = TestClient(app)

    response = client.post("/plivo/status", json={"StreamID": "stream-1", "Event": "stream"})

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
