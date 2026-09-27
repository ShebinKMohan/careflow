from typing import Any

import pytest
from pydantic import ValidationError

from voice_agent.config.settings import Settings

STRONG_CONTROL_API_KEY = "control-secret-0123456789abcdef012345"
STRONG_STREAM_AUTH_TOKEN = "stream-secret-0123456789abcdef012345"


def test_production_rejects_local_provider_backends() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="production",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
        )


def test_production_requires_plivo_request_and_stream_secrets() -> None:
    with pytest.raises(ValidationError):
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
        )


def test_production_rejects_blank_plivo_call_control_values() -> None:
    for field in ("plivo_auth_id", "plivo_auth_token", "plivo_from_number"):
        with pytest.raises(ValidationError):
            _valid_production_settings(**{field: "   "})


def test_production_rejects_invalid_plivo_from_number() -> None:
    for invalid_number in ("14155550100", "+01234567890", "+1415555", "+1415555010abc"):
        with pytest.raises(ValidationError, match=r"plivo_from_number must be an E\.164"):
            _valid_production_settings(plivo_from_number=invalid_number)


def test_production_rejects_blank_provider_credentials_and_identifiers() -> None:
    for field in (
        "deepgram_api_key",
        "elevenlabs_api_key",
        "elevenlabs_voice_id",
        "elevenlabs_model_id",
    ):
        with pytest.raises(ValidationError):
            _valid_production_settings(**{field: "   "})


def test_production_rejects_blank_internal_secrets() -> None:
    with pytest.raises(ValidationError, match="control_api_key must not be blank"):
        _valid_production_settings(control_api_key=" " * 32)
    with pytest.raises(ValidationError, match="stream_auth_token must not be blank"):
        _valid_production_settings(stream_auth_token=" " * 32)


def test_production_rejects_weak_control_api_key() -> None:
    with pytest.raises(ValidationError, match="control_api_key must be at least 32 characters"):
        _valid_production_settings(control_api_key="short-control")


def test_production_rejects_weak_stream_auth_token() -> None:
    with pytest.raises(ValidationError, match="stream_auth_token must be at least 32 characters"):
        _valid_production_settings(stream_auth_token="short-stream")


@pytest.mark.parametrize(
    "orchestrator_url",
    [
        "http://127.0.0.1:8091/voice/respond",
        "http://localhost:8091/voice/respond",
        "http://[::1]:8091/voice/respond",
    ],
)
def test_production_allows_http_loopback_orchestrator_sidecar(
    orchestrator_url: str,
) -> None:
    settings = _valid_production_settings(orchestrator_url=f" {orchestrator_url}/ ")

    assert settings.orchestrator_url == orchestrator_url


def test_production_rejects_remote_http_orchestrator_url() -> None:
    with pytest.raises(ValidationError, match="production requires orchestrator_url to use https"):
        _valid_production_settings(orchestrator_url="http://orchestrator.example.com/respond")


def test_production_allows_loopback_luxtts_sidecar_without_api_key() -> None:
    settings = Settings(
        environment="production",
        public_base_url="https://voice.example.com",
        session_backend="redis",
        redis_url="redis://redis:6379/0",
        stt_provider="deepgram",
        deepgram_api_key="deepgram-secret",
        llm_provider="orchestrator",
        orchestrator_url="https://orchestrator.example.com/respond",
        orchestrator_api_key="orchestrator-secret",
        tts_provider="luxtts",
        luxtts_tts_url=" http://127.0.0.1:8811/tts/ ",
        plivo_auth_token="plivo-auth-token",
        plivo_auth_id="plivo-auth-id",
        plivo_from_number="+14155550100",
        control_api_key=STRONG_CONTROL_API_KEY,
        stream_auth_token=STRONG_STREAM_AUTH_TOKEN,
    )

    assert settings.luxtts_tts_url == "http://127.0.0.1:8811/tts"


def test_production_rejects_remote_http_luxtts_sidecar() -> None:
    with pytest.raises(ValidationError, match="production requires luxtts_tts_url to use https"):
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
            tts_provider="luxtts",
            luxtts_tts_url="http://luxtts.example.com/tts",
            luxtts_api_key="lux-secret",
            plivo_auth_token="plivo-auth-token",
            plivo_auth_id="plivo-auth-id",
            plivo_from_number="+14155550100",
            control_api_key=STRONG_CONTROL_API_KEY,
            stream_auth_token=STRONG_STREAM_AUTH_TOKEN,
        )


def test_production_requires_luxtts_api_key_for_remote_sidecar() -> None:
    with pytest.raises(ValidationError, match="production requires luxtts_api_key"):
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
            tts_provider="luxtts",
            luxtts_tts_url="https://luxtts.example.com/tts",
            plivo_auth_token="plivo-auth-token",
            plivo_auth_id="plivo-auth-id",
            plivo_from_number="+14155550100",
            control_api_key=STRONG_CONTROL_API_KEY,
            stream_auth_token=STRONG_STREAM_AUTH_TOKEN,
        )


def test_luxtts_provider_requires_endpoint() -> None:
    with pytest.raises(ValidationError, match="luxtts_tts_url is required"):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="luxtts",
        )


@pytest.mark.parametrize(
    ("orchestrator_url", "match"),
    [
        ("https://", "orchestrator_url must be a valid http or https URL with a host"),
        ("https:///respond", "orchestrator_url must be a valid http or https URL with a host"),
        (
            "https://orchestrator.example.com/respond?tenant=acme",
            "orchestrator_url must not include a query string or fragment",
        ),
        (
            "https://orchestrator.example.com/respond#voice",
            "orchestrator_url must not include a query string or fragment",
        ),
        ("ftp://orchestrator.example.com/respond", "orchestrator_url must use http or https"),
    ],
)
def test_settings_rejects_malformed_orchestrator_url(
    orchestrator_url: str,
    match: str,
) -> None:
    with pytest.raises(ValidationError, match=match):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="orchestrator",
            orchestrator_url=orchestrator_url,
            tts_provider="local",
        )


def test_settings_allows_http_orchestrator_url_outside_production() -> None:
    settings = Settings(
        environment="test",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="orchestrator",
        orchestrator_url=" http://localhost:9000/voice/respond/ ",
        tts_provider="local",
    )

    assert settings.orchestrator_url == "http://localhost:9000/voice/respond"


def test_settings_normalizes_runtime_urls() -> None:
    settings = Settings(
        environment="test",
        public_base_url=" https://voice.example.com/ ",
        session_backend="redis",
        redis_url=" redis://redis:6379/0 ",
        stt_provider="local",
        llm_provider="orchestrator",
        orchestrator_url=" https://orchestrator.example.com/respond ",
        tts_provider="local",
        plivo_api_base_url=" https://api.plivo.com/ ",
    )

    assert settings.public_base_url == "https://voice.example.com"
    assert settings.media_websocket_url == "wss://voice.example.com/plivo/media"
    assert settings.redis_url == "redis://redis:6379/0"
    assert settings.orchestrator_url == "https://orchestrator.example.com/respond"
    assert settings.plivo_api_base_url == "https://api.plivo.com"


@pytest.mark.parametrize(
    "redis_url",
    [
        "redis://redis:6379/0",
        "rediss://:secret@redis.example.com:6380/1?ssl_cert_reqs=required",
        "unix:///var/run/redis/redis.sock?db=0",
    ],
)
def test_settings_accepts_supported_redis_url_schemes(redis_url: str) -> None:
    settings = Settings(
        environment="test",
        public_base_url="https://voice.example.com",
        session_backend="redis",
        redis_url=f" {redis_url} ",
        stt_provider="local",
        llm_provider="local",
        tts_provider="local",
    )

    assert settings.redis_url == redis_url


@pytest.mark.parametrize(
    ("redis_url", "match"),
    [
        ("http://redis:6379/0", "redis_url must use redis, rediss, or unix scheme"),
        ("redis://", "redis_url must include a host when using redis or rediss"),
        ("rediss:///0", "redis_url must include a host when using redis or rediss"),
        ("redis://redis:not-a-port/0", "redis_url has invalid port"),
        ("unix://", "redis_url must include a socket path when using unix"),
    ],
)
def test_settings_rejects_malformed_redis_urls_for_redis_backend(
    redis_url: str,
    match: str,
) -> None:
    with pytest.raises(ValidationError, match=match):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="redis",
            redis_url=redis_url,
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
        )


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"public_base_url": "https://"}, "public_base_url must be a valid https URL with a host"),
        (
            {"public_base_url": "https:///voice"},
            "public_base_url must be a valid https URL with a host",
        ),
        (
            {"public_base_url": "https://voice.example.com?tenant=acme"},
            "public_base_url must not include a query string or fragment",
        ),
        (
            {"public_base_url": "https://voice.example.com#callbacks"},
            "public_base_url must not include a query string or fragment",
        ),
        (
            {"plivo_api_base_url": "https://"},
            "plivo_api_base_url must be a valid https URL with a host",
        ),
        (
            {"plivo_api_base_url": "https:///api"},
            "plivo_api_base_url must be a valid https URL with a host",
        ),
        (
            {"plivo_api_base_url": "https://api.plivo.com?region=us"},
            "plivo_api_base_url must not include a query string or fragment",
        ),
        (
            {"plivo_api_base_url": "https://api.plivo.com#v1"},
            "plivo_api_base_url must not include a query string or fragment",
        ),
    ],
)
def test_settings_rejects_malformed_runtime_https_base_urls(
    overrides: dict[str, str],
    match: str,
) -> None:
    arguments: dict[str, Any] = {
        "environment": "test",
        "public_base_url": "https://voice.example.com",
        "session_backend": "memory",
        "stt_provider": "local",
        "llm_provider": "local",
        "tts_provider": "local",
    }
    arguments.update(overrides)

    with pytest.raises(ValidationError, match=match):
        Settings(**arguments)


def test_settings_accepts_runtime_https_base_url_path_prefixes() -> None:
    settings = Settings(
        environment="test",
        public_base_url="https://voice.example.com/voice-agent/",
        session_backend="memory",
        stt_provider="local",
        llm_provider="local",
        tts_provider="local",
        plivo_api_base_url="https://api.plivo.com/v1/",
    )

    assert settings.public_base_url == "https://voice.example.com/voice-agent"
    assert settings.answer_callback_url == "https://voice.example.com/voice-agent/plivo/answer"
    assert settings.plivo_api_base_url == "https://api.plivo.com/v1"


def test_production_normalizes_call_control_and_provider_identifiers() -> None:
    settings = _valid_production_settings(
        plivo_auth_id=" plivo-auth-id ",
        plivo_from_number=" +14155550100 ",
        elevenlabs_voice_id=" voice-1 ",
        elevenlabs_model_id=" eleven_flash_v2_5 ",
    )

    assert settings.plivo_auth_id == "plivo-auth-id"
    assert settings.plivo_from_number == "+14155550100"
    assert settings.elevenlabs_voice_id == "voice-1"
    assert settings.elevenlabs_model_id == "eleven_flash_v2_5"


def test_production_normalizes_secret_values() -> None:
    settings = _valid_production_settings(
        control_api_key=f" {STRONG_CONTROL_API_KEY} ",
        stream_auth_token=f" {STRONG_STREAM_AUTH_TOKEN} ",
        plivo_auth_token=" plivo-auth-token ",
        deepgram_api_key=" deepgram-secret ",
        orchestrator_api_key=" orchestrator-secret ",
        elevenlabs_api_key=" eleven-secret ",
    )

    assert settings.control_api_key is not None
    assert settings.control_api_key.get_secret_value() == STRONG_CONTROL_API_KEY
    assert settings.stream_auth_token is not None
    assert settings.stream_auth_token.get_secret_value() == STRONG_STREAM_AUTH_TOKEN
    assert settings.plivo_auth_token is not None
    assert settings.plivo_auth_token.get_secret_value() == "plivo-auth-token"
    assert settings.deepgram_api_key is not None
    assert settings.deepgram_api_key.get_secret_value() == "deepgram-secret"
    assert settings.orchestrator_api_key is not None
    assert settings.orchestrator_api_key.get_secret_value() == "orchestrator-secret"
    assert settings.elevenlabs_api_key is not None
    assert settings.elevenlabs_api_key.get_secret_value() == "eleven-secret"


def test_local_environment_can_use_deterministic_providers() -> None:
    settings = Settings(
        environment="local",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="local",
        tts_provider="local",
    )

    assert settings.media_websocket_url == "wss://voice.example.com/plivo/media"
    assert settings.status_callback_url == "https://voice.example.com/plivo/status"


def test_stream_auth_secret_is_not_embedded_in_base_media_websocket_url() -> None:
    settings = Settings(
        environment="test",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="local",
        tts_provider="local",
        stream_auth_token="stream-secret",
    )

    assert settings.media_websocket_url == "wss://voice.example.com/plivo/media"


def test_default_elevenlabs_model_is_real_time_flash() -> None:
    settings = Settings(
        environment="test",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="local",
        tts_provider="local",
    )

    assert settings.elevenlabs_model_id == "eleven_flash_v2_5"


def test_stream_token_ttl_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            stream_token_ttl_seconds=0,
        )


def test_call_session_ttl_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            call_session_ttl_seconds=0,
        )


def test_call_event_stream_maxlen_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            call_event_stream_maxlen=0,
        )


def test_callback_max_body_bytes_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            callback_max_body_bytes=0,
        )


def test_control_max_body_bytes_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            control_max_body_bytes=0,
        )


def test_media_message_max_bytes_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            media_message_max_bytes=0,
        )


def test_max_transcript_chars_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            max_transcript_chars=0,
        )


def test_stream_noise_cancellation_level_must_be_within_plivo_range() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            stream_noise_cancellation=True,
            stream_noise_cancellation_level=59,
        )


def test_fallback_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            fallback_timeout_seconds=0,
        )


def test_audio_ingest_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            audio_ingest_timeout_seconds=0,
        )


def test_plivo_send_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            plivo_send_timeout_seconds=0,
        )


def test_provider_close_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            provider_close_timeout_seconds=0,
        )


def test_media_start_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            media_start_timeout_seconds=0,
        )


def test_plivo_api_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            plivo_api_timeout_seconds=0,
        )


def test_plivo_api_max_response_bytes_must_be_positive() -> None:
    with pytest.raises(ValidationError, match="plivo_api_max_response_bytes must be positive"):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            plivo_api_max_response_bytes=0,
        )


def test_orchestrator_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            orchestrator_timeout_seconds=0,
        )


def test_orchestrator_first_chunk_timeout_must_be_positive() -> None:
    with pytest.raises(
        ValidationError,
        match="orchestrator_first_chunk_timeout_seconds must be positive",
    ):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            orchestrator_first_chunk_timeout_seconds=0,
        )


def test_settings_defaults_speculative_response_prewarm_without_extra_delay() -> None:
    settings = Settings(
        environment="test",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="local",
        tts_provider="local",
    )

    assert settings.speculative_response_prewarm
    assert settings.speculative_response_stability_ms == 0
    assert settings.orchestrator_require_streaming_response


def test_settings_defaults_sarvam_streaming_buffer_to_provider_minimum() -> None:
    settings = Settings(
        environment="test",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="local",
        tts_provider="local",
    )

    assert settings.sarvam_min_buffer_size == 50


def test_sarvam_min_buffer_size_must_respect_provider_minimum() -> None:
    with pytest.raises(ValidationError, match="sarvam_min_buffer_size must be at least 50"):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            sarvam_min_buffer_size=20,
        )


def test_readiness_check_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            readiness_check_timeout_seconds=0,
        )


def test_orchestrator_max_response_bytes_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            orchestrator_max_response_bytes=0,
        )


def test_max_reply_chars_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            max_reply_chars=0,
        )


def test_max_play_audio_bytes_must_be_even_integer_of_at_least_two() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            max_play_audio_bytes=3,
        )


def test_pre_response_ack_delay_must_be_non_negative() -> None:
    with pytest.raises(ValidationError, match="pre_response_ack_delay_ms must be non-negative"):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            pre_response_ack_delay_ms=-1,
        )


def test_blank_pre_response_ack_text_is_rejected() -> None:
    with pytest.raises(ValidationError, match="pre_response_ack_text must not be blank"):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            pre_response_ack_text="   ",
        )


def test_pre_response_ack_text_must_be_short() -> None:
    with pytest.raises(ValidationError, match="pre_response_ack_text must be at most 90"):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            pre_response_ack_text="x" * 91,
        )


def test_elevenlabs_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            elevenlabs_timeout_seconds=0,
        )


def test_elevenlabs_max_audio_bytes_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            elevenlabs_max_audio_bytes=0,
        )


def test_deepgram_websocket_timing_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            deepgram_connect_timeout_seconds=0,
        )
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            deepgram_ping_interval_seconds=0,
        )
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            deepgram_ping_timeout_seconds=0,
        )


def test_deepgram_endpointing_must_be_positive() -> None:
    with pytest.raises(ValidationError, match="deepgram_endpointing_ms must be positive"):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            deepgram_endpointing_ms=0,
        )


def test_deepgram_endpointing_default_is_tuned_for_phone_agent_latency() -> None:
    settings = Settings(
        environment="test",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="local",
        tts_provider="local",
    )

    assert settings.deepgram_endpointing_ms == 150


def test_local_end_of_turn_silence_default_is_tuned_for_phone_agent_latency() -> None:
    settings = Settings(
        environment="test",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="local",
        tts_provider="local",
    )

    assert settings.local_end_of_turn_silence_ms == 450


def test_fast_final_end_of_turn_silence_default_is_lower_than_general_silence() -> None:
    settings = Settings(
        environment="test",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="local",
        tts_provider="local",
    )

    assert settings.fast_final_end_of_turn_silence_ms == 200
    assert settings.fast_final_end_of_turn_silence_ms < settings.local_end_of_turn_silence_ms


def test_interim_transcript_stability_default_is_conservative() -> None:
    settings = Settings(
        environment="test",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="local",
        tts_provider="local",
    )

    assert settings.interim_transcript_stability_ms == 450


def test_interim_local_silence_commit_default_preserves_quality() -> None:
    settings = Settings(
        environment="test",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="local",
        tts_provider="local",
    )

    assert not settings.interim_local_silence_commit_enabled


def test_speculative_response_prewarm_default_is_enabled() -> None:
    settings = Settings(
        environment="test",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="local",
        tts_provider="local",
    )

    assert settings.speculative_response_prewarm


def test_speculative_response_stability_default_starts_before_commit_stability() -> None:
    settings = Settings(
        environment="test",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="local",
        tts_provider="local",
    )

    assert settings.speculative_response_stability_ms == 0
    assert settings.speculative_response_stability_ms < settings.interim_transcript_stability_ms


def test_speculative_response_min_chars_default_avoids_tiny_paid_interims() -> None:
    settings = Settings(
        environment="test",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="local",
        tts_provider="local",
    )

    assert settings.speculative_response_min_chars == 8


def test_local_end_of_turn_silence_must_be_non_negative() -> None:
    with pytest.raises(
        ValidationError,
        match="local_end_of_turn_silence_ms must be non-negative",
    ):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            local_end_of_turn_silence_ms=-1,
        )


def test_fast_final_end_of_turn_silence_must_be_non_negative() -> None:
    with pytest.raises(
        ValidationError,
        match="fast_final_end_of_turn_silence_ms must be non-negative",
    ):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            fast_final_end_of_turn_silence_ms=-1,
        )


def test_interim_transcript_stability_must_be_non_negative() -> None:
    with pytest.raises(
        ValidationError,
        match="interim_transcript_stability_ms must be non-negative",
    ):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            interim_transcript_stability_ms=-1,
        )


def test_speculative_response_stability_must_be_non_negative() -> None:
    with pytest.raises(
        ValidationError,
        match="speculative_response_stability_ms must be non-negative",
    ):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            speculative_response_stability_ms=-1,
        )


def test_speculative_response_min_chars_must_be_non_negative() -> None:
    with pytest.raises(
        ValidationError,
        match="speculative_response_min_chars must be non-negative",
    ):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            speculative_response_min_chars=-1,
        )


def test_luxtts_chunking_defaults_are_quality_safe() -> None:
    settings = Settings(
        environment="test",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="local",
        tts_provider="local",
    )

    assert settings.luxtts_min_first_chunk_chars == 36
    assert settings.luxtts_min_soft_chunk_chars == 42
    assert settings.luxtts_max_soft_chunk_chars == 96


def test_luxtts_chunking_values_must_be_ordered() -> None:
    with pytest.raises(
        ValidationError,
        match="luxtts_max_soft_chunk_chars must be >= luxtts_min_soft_chunk_chars",
    ):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            luxtts_min_soft_chunk_chars=80,
            luxtts_max_soft_chunk_chars=72,
        )


def test_deepgram_utterance_end_must_respect_provider_minimum() -> None:
    with pytest.raises(ValidationError, match="deepgram_utterance_end_ms must be at least 1000"):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            deepgram_utterance_end_ms=999,
        )


def test_deepgram_transcript_queue_max_size_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            deepgram_transcript_queue_max_size=0,
        )


def test_deepgram_transcript_message_max_bytes_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            deepgram_transcript_message_max_bytes=0,
        )


def test_deepgram_audio_queue_max_size_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            deepgram_audio_queue_max_size=0,
        )


def test_pre_response_ack_is_disabled_by_default() -> None:
    settings = Settings(
        public_base_url="https://voice.example.com",
        stt_provider="local",
        llm_provider="local",
        tts_provider="local",
    )

    assert settings.pre_response_ack_text is None


def _local_speech_args(**overrides: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "environment": "test",
        "public_base_url": "https://voice.example.com",
        "session_backend": "memory",
        "stt_provider": "sherpa",
        "llm_provider": "local",
        "tts_provider": "kokoro",
    }
    args.update(overrides)
    return args


def test_stt_provider_sherpa_accepted_with_default_model_paths() -> None:
    settings = Settings(**_local_speech_args())

    assert settings.stt_provider == "sherpa"
    assert settings.sherpa_stt_model_dir == (
        "models/sherpa-onnx-streaming-zipformer-en-2023-06-26"
    )
    assert settings.sherpa_stt_vad_model_path == "models/silero_vad.onnx"
    assert settings.sherpa_stt_smart_turn_model_path == "models/smart-turn-v3.2-cpu.onnx"


def test_sherpa_requires_model_dir_and_vad_path() -> None:
    for field in ("sherpa_stt_model_dir", "sherpa_stt_vad_model_path"):
        with pytest.raises(ValidationError, match=f"{field} is required"):
            Settings(**_local_speech_args(**{field: "   "}))


def test_sherpa_silence_gate_ordering_is_enforced() -> None:
    with pytest.raises(ValidationError, match="sherpa_stt_semantic_silence_ms must be positive"):
        Settings(**_local_speech_args(sherpa_stt_semantic_silence_ms=0))
    with pytest.raises(
        ValidationError,
        match="sherpa_stt_endpoint_silence_ms must be >= sherpa_stt_semantic_silence_ms",
    ):
        Settings(
            **_local_speech_args(
                sherpa_stt_semantic_silence_ms=500,
                sherpa_stt_endpoint_silence_ms=400,
            )
        )
    with pytest.raises(
        ValidationError,
        match="sherpa_stt_hard_silence_ms must be >= sherpa_stt_endpoint_silence_ms",
    ):
        Settings(
            **_local_speech_args(
                sherpa_stt_endpoint_silence_ms=800,
                sherpa_stt_hard_silence_ms=700,
            )
        )


def test_sherpa_smart_turn_threshold_bounds_are_enforced() -> None:
    for threshold in (0.0, 1.0):
        with pytest.raises(
            ValidationError,
            match="sherpa_stt_smart_turn_threshold must be between 0 and 1",
        ):
            Settings(**_local_speech_args(sherpa_stt_smart_turn_threshold=threshold))


def test_blank_smart_turn_model_path_normalizes_to_none() -> None:
    settings = Settings(**_local_speech_args(sherpa_stt_smart_turn_model_path="   "))

    assert settings.sherpa_stt_smart_turn_model_path is None


def test_tts_provider_kokoro_accepted_with_defaults() -> None:
    settings = Settings(**_local_speech_args())

    assert settings.tts_provider == "kokoro"
    assert settings.kokoro_model_path == "models/kokoro-v1.0.onnx"
    assert settings.kokoro_voices_path == "models/voices-v1.0.bin"
    assert settings.kokoro_voice == "af_heart"
    assert settings.kokoro_lang == "en-us"


def test_kokoro_requires_model_voices_voice_and_lang_fields() -> None:
    for field in ("kokoro_model_path", "kokoro_voices_path", "kokoro_voice", "kokoro_lang"):
        with pytest.raises(ValidationError, match=f"{field} is required"):
            Settings(**_local_speech_args(**{field: "   "}))


def test_kokoro_speed_must_be_positive() -> None:
    with pytest.raises(ValidationError, match="kokoro_speed must be positive"):
        Settings(**_local_speech_args(kokoro_speed=0))


def test_kokoro_chunk_chars_ordering_is_enforced() -> None:
    with pytest.raises(
        ValidationError,
        match="kokoro_max_soft_chunk_chars must be >= kokoro_min_soft_chunk_chars",
    ):
        Settings(
            **_local_speech_args(
                kokoro_min_soft_chunk_chars=80,
                kokoro_max_soft_chunk_chars=72,
            )
        )


def test_production_allows_cpu_local_sherpa_and_kokoro_providers() -> None:
    # Unlike stt/tts_provider="local", the in-process sherpa/kokoro engines are real
    # production backends and must pass validate_runtime_safety.
    settings = Settings(
        environment="production",
        public_base_url="https://voice.example.com",
        session_backend="redis",
        redis_url="redis://redis:6379/0",
        stt_provider="sherpa",
        llm_provider="orchestrator",
        orchestrator_url="https://orchestrator.example.com/respond",
        orchestrator_api_key="orchestrator-secret",
        tts_provider="kokoro",
        plivo_auth_token="plivo-auth-token",
        plivo_auth_id="plivo-auth-id",
        plivo_from_number="+14155550100",
        control_api_key=STRONG_CONTROL_API_KEY,
        stream_auth_token=STRONG_STREAM_AUTH_TOKEN,
    )

    assert settings.environment == "production"
    assert settings.stt_provider == "sherpa"
    assert settings.tts_provider == "kokoro"


def _valid_production_settings(
    *,
    control_api_key: str = STRONG_CONTROL_API_KEY,
    stream_auth_token: str = STRONG_STREAM_AUTH_TOKEN,
    orchestrator_url: str = "https://orchestrator.example.com/respond",
    plivo_auth_id: str = "plivo-auth-id",
    plivo_auth_token: str = "plivo-auth-token",
    plivo_from_number: str = "+14155550100",
    deepgram_api_key: str = "deepgram-secret",
    orchestrator_api_key: str = "orchestrator-secret",
    elevenlabs_api_key: str = "eleven-secret",
    elevenlabs_voice_id: str = "voice-1",
    elevenlabs_model_id: str = "eleven_flash_v2_5",
) -> Settings:
    return Settings(
        environment="production",
        public_base_url="https://voice.example.com",
        session_backend="redis",
        redis_url="redis://redis:6379/0",
        stt_provider="deepgram",
        deepgram_api_key=deepgram_api_key,
        llm_provider="orchestrator",
        orchestrator_url=orchestrator_url,
        orchestrator_api_key=orchestrator_api_key,
        tts_provider="elevenlabs",
        elevenlabs_api_key=elevenlabs_api_key,
        elevenlabs_voice_id=elevenlabs_voice_id,
        elevenlabs_model_id=elevenlabs_model_id,
        plivo_auth_token=plivo_auth_token,
        plivo_auth_id=plivo_auth_id,
        plivo_from_number=plivo_from_number,
        control_api_key=control_api_key,
        stream_auth_token=stream_auth_token,
    )
