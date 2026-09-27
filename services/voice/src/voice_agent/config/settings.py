from __future__ import annotations

import re
from functools import lru_cache
from ipaddress import ip_address
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from voice_agent.media.limits import MAX_PRE_RESPONSE_ACK_CHARS

Environment = Literal["local", "test", "production"]
SessionBackend = Literal["memory", "redis"]
STTProvider = Literal["local", "deepgram", "sherpa"]
LLMProvider = Literal["local", "orchestrator", "google_live"]
TTSProvider = Literal["local", "elevenlabs", "sarvam", "luxtts", "kokoro"]
HTTPMethod = Literal["GET", "POST"]
MIN_INTERNAL_SECRET_LENGTH = 32
DEFAULT_CALL_SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
DEFAULT_CALL_EVENT_STREAM_MAXLEN = 100_000
DEFAULT_CALLBACK_MAX_BODY_BYTES = 16 * 1024
DEFAULT_CONTROL_MAX_BODY_BYTES = 4 * 1024
DEFAULT_MEDIA_MESSAGE_MAX_BYTES = 64 * 1024
DEFAULT_PLIVO_API_MAX_RESPONSE_BYTES = 64 * 1024
DEFAULT_ORCHESTRATOR_MAX_RESPONSE_BYTES = 64 * 1024
DEFAULT_ORCHESTRATOR_FIRST_CHUNK_TIMEOUT_SECONDS = 5.0
DEFAULT_ORCHESTRATOR_REQUIRE_STREAMING_RESPONSE = True
DEFAULT_ELEVENLABS_MAX_AUDIO_BYTES = 512 * 1024
DEFAULT_LUXTTS_MAX_AUDIO_BYTES = 2 * 1024 * 1024
DEFAULT_LUXTTS_NUM_STEPS = 4
DEFAULT_LUXTTS_T_SHIFT = 0.9
DEFAULT_LUXTTS_SPEED = 0.8
DEFAULT_LUXTTS_MIN_FIRST_CHUNK_CHARS = 36
DEFAULT_LUXTTS_MIN_SOFT_CHUNK_CHARS = 42
DEFAULT_LUXTTS_MAX_SOFT_CHUNK_CHARS = 96
DEFAULT_SHERPA_STT_MODEL_DIR = "models/sherpa-onnx-streaming-zipformer-en-2023-06-26"
DEFAULT_SHERPA_STT_VAD_MODEL_PATH = "models/silero_vad.onnx"
DEFAULT_SHERPA_STT_NUM_THREADS = 2
DEFAULT_SHERPA_STT_SEMANTIC_SILENCE_MS = 200
DEFAULT_SHERPA_STT_ENDPOINT_SILENCE_MS = 400
DEFAULT_SHERPA_STT_HARD_SILENCE_MS = 900
DEFAULT_SHERPA_STT_SMART_TURN_MODEL_PATH = "models/smart-turn-v3.2-cpu.onnx"
DEFAULT_SHERPA_STT_SMART_TURN_THRESHOLD = 0.5
DEFAULT_KOKORO_MODEL_PATH = "models/kokoro-v1.0.onnx"
DEFAULT_KOKORO_VOICES_PATH = "models/voices-v1.0.bin"
DEFAULT_KOKORO_VOICE = "af_heart"
DEFAULT_KOKORO_SPEED = 1.0
DEFAULT_KOKORO_LANG = "en-us"
DEFAULT_KOKORO_MIN_FIRST_CHUNK_CHARS = 24
DEFAULT_KOKORO_MIN_SOFT_CHUNK_CHARS = 36
DEFAULT_KOKORO_MAX_SOFT_CHUNK_CHARS = 96
DEFAULT_KOKORO_FIRST_UNIT_MAX_CHARS = 20
DEFAULT_DEEPGRAM_ENDPOINTING_MS = 150
DEFAULT_DEEPGRAM_UTTERANCE_END_MS = 1000
DEFAULT_DEEPGRAM_TRANSCRIPT_QUEUE_MAX_SIZE = 100
DEFAULT_DEEPGRAM_TRANSCRIPT_MESSAGE_MAX_BYTES = 64 * 1024
DEFAULT_DEEPGRAM_AUDIO_QUEUE_MAX_SIZE = 250
DEFAULT_MAX_TRANSCRIPT_CHARS = 4096
DEFAULT_MAX_REPLY_CHARS = 1000
DEFAULT_MAX_PLAY_AUDIO_BYTES = 6400
DEFAULT_PRE_RESPONSE_ACK_TEXT: str | None = None
DEFAULT_PRE_RESPONSE_ACK_DELAY_MS = 350
DEFAULT_LOCAL_END_OF_TURN_SILENCE_MS = 450
DEFAULT_FAST_FINAL_END_OF_TURN_SILENCE_MS = 200
DEFAULT_INTERIM_TRANSCRIPT_STABILITY_MS = 450
DEFAULT_INTERIM_LOCAL_SILENCE_COMMIT_ENABLED = False
DEFAULT_SPECULATIVE_RESPONSE_PREWARM = True
DEFAULT_SPECULATIVE_RESPONSE_STABILITY_MS = 0
DEFAULT_SPECULATIVE_RESPONSE_MIN_CHARS = 8
MIN_SARVAM_MIN_BUFFER_SIZE = 50
E164_PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="VOICE_AGENT_",
        extra="ignore",
        frozen=True,
    )

    environment: Environment = "local"
    public_base_url: str = Field(..., description="Public HTTPS base URL for Plivo callbacks.")

    # Structured logging. log_format="json" emits one JSON object per line (call_id/stream_id
    # carried through), "text" is human-friendly for local dev.
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"

    session_backend: SessionBackend = "redis"
    redis_url: str | None = "redis://redis:6379/0"
    # Bound every Redis command/connect so a hung or half-open Redis (failover, partition,
    # idle LB drop) raises promptly instead of stalling the realtime media path forever.
    redis_socket_timeout_seconds: float = 2.0
    redis_connect_timeout_seconds: float = 2.0
    redis_health_check_interval_seconds: float = 30.0
    # Bound each Redis client's connection pool so a burst of concurrent calls cannot open an
    # unbounded number of connections (three clients: events, sessions, per-call config).
    redis_max_connections: int = 50

    stt_provider: STTProvider = "deepgram"
    llm_provider: LLMProvider = "orchestrator"
    tts_provider: TTSProvider = "elevenlabs"

    stream_timeout_seconds: int = 60 * 60
    stream_token_ttl_seconds: int = 5 * 60
    call_session_ttl_seconds: int = DEFAULT_CALL_SESSION_TTL_SECONDS
    call_event_stream_maxlen: int = DEFAULT_CALL_EVENT_STREAM_MAXLEN
    readiness_check_timeout_seconds: float = 1.0
    # On shutdown, /readyz flips to not_ready (so the load balancer stops routing new calls)
    # and the lifespan waits up to this long for in-flight media calls to drain before the
    # Redis/HTTP clients are closed. Pair with uvicorn's graceful-shutdown timeout and a
    # platform preStop hook / terminationGracePeriod longer than a typical call.
    shutdown_grace_seconds: float = 25.0
    callback_max_body_bytes: int = DEFAULT_CALLBACK_MAX_BODY_BYTES
    control_max_body_bytes: int = DEFAULT_CONTROL_MAX_BODY_BYTES
    media_message_max_bytes: int = DEFAULT_MEDIA_MESSAGE_MAX_BYTES
    media_start_timeout_seconds: float = 5.0
    turn_timeout_seconds: float = 12.0
    fallback_timeout_seconds: float = 3.0
    audio_ingest_timeout_seconds: float = 1.0
    plivo_send_timeout_seconds: float = 1.0
    provider_close_timeout_seconds: float = 2.0
    # Transient-failure retry for outbound provider calls (LLM/TTS). A provider that returns
    # 429/5xx or drops the connection BEFORE any audio/text is streamed is retried this many
    # total attempts (1 = no retry) with capped exponential backoff, honoring Retry-After.
    # Kept small so the total stays well under turn_timeout_seconds; never retries mid-stream.
    provider_retry_max_attempts: int = 2
    provider_retry_backoff_seconds: float = 0.25
    provider_retry_max_backoff_seconds: float = 2.0
    max_transcript_chars: int = DEFAULT_MAX_TRANSCRIPT_CHARS
    max_reply_chars: int = DEFAULT_MAX_REPLY_CHARS
    # Per-call safety budget: end the call after this many caller turns to bound provider
    # spend if a runaway loop (echo/barge-in feedback) occurs. 0 disables the cap. Call
    # duration is independently bounded by Plivo time_limit + stream_timeout.
    max_turns_per_call: int = 200
    # Debounce window for persisting the (full, growing) call transcript. Bursts of turns
    # coalesce into at most one locked read-modify-write upsert per interval, with a trailing
    # flush so the final transcript is always written. 0 persists every turn immediately.
    transcript_persist_interval_seconds: float = 2.0
    max_play_audio_bytes: int = DEFAULT_MAX_PLAY_AUDIO_BYTES
    pre_response_ack_text: str | None = DEFAULT_PRE_RESPONSE_ACK_TEXT
    pre_response_ack_delay_ms: int = DEFAULT_PRE_RESPONSE_ACK_DELAY_MS
    local_end_of_turn_silence_ms: int = DEFAULT_LOCAL_END_OF_TURN_SILENCE_MS
    fast_final_end_of_turn_silence_ms: int = DEFAULT_FAST_FINAL_END_OF_TURN_SILENCE_MS
    interim_transcript_stability_ms: int = DEFAULT_INTERIM_TRANSCRIPT_STABILITY_MS
    interim_local_silence_commit_enabled: bool = (
        DEFAULT_INTERIM_LOCAL_SILENCE_COMMIT_ENABLED
    )
    speculative_response_prewarm: bool = DEFAULT_SPECULATIVE_RESPONSE_PREWARM
    speculative_response_stability_ms: int = DEFAULT_SPECULATIVE_RESPONSE_STABILITY_MS
    speculative_response_min_chars: int = DEFAULT_SPECULATIVE_RESPONSE_MIN_CHARS
    barge_in_min_frames: int = 2
    barge_in_energy_threshold: float = 200.0
    # Opt-in: send Plivo `checkpoint` events after agent audio and treat the matching
    # `playedStream` ack as the authoritative playback-finished signal (more accurate than the
    # local duration estimate, which stays as the fallback). Default off pending live
    # verification of ack timing on your Plivo account; the ack can only END playback earlier,
    # never extend it, so enabling it cannot make barge-in/end-of-turn detection slower.
    playback_checkpoints_enabled: bool = False
    # Rate limiting for the billable control endpoints (/preview/*, /calls/outbound). Process-
    # local sliding window keyed by control credential; bounds per-instance provider/dial spend.
    rate_limit_enabled: bool = True
    control_rate_limit_per_minute: int = 60
    control_api_key: SecretStr | None = None
    plivo_auth_id: str | None = None
    plivo_auth_token: SecretStr | None = None
    plivo_from_number: str | None = None
    plivo_api_base_url: str = "https://api.plivo.com"
    plivo_api_timeout_seconds: float = 10.0
    plivo_api_max_response_bytes: int = DEFAULT_PLIVO_API_MAX_RESPONSE_BYTES
    outbound_hangup_on_ring_seconds: int = 45
    outbound_time_limit_seconds: int = 15 * 60
    # Dev/test switch: when true, /calls/outbound records the call and returns success
    # WITHOUT placing a real Plivo call (no dialing, no cost) — so the full
    # dashboard -> control-plane -> agent chain is testable safely. FORCE-DISABLED in
    # production, where outbound always dials for real ("auto mode").
    outbound_dry_run: bool = False
    stream_auth_token: SecretStr | None = None
    # Replay protection (opt-in; default off so legitimate Plivo reconnects/retries are never
    # rejected). stream_token_single_use rejects a stream token reused within its TTL;
    # webhook_replay_protection rejects a Plivo callback whose V3 nonce was already seen.
    stream_token_single_use: bool = False
    webhook_replay_protection: bool = False
    webhook_nonce_ttl_seconds: int = 300
    deepgram_api_key: SecretStr | None = None
    deepgram_endpointing_ms: int = DEFAULT_DEEPGRAM_ENDPOINTING_MS
    deepgram_utterance_end_ms: int = DEFAULT_DEEPGRAM_UTTERANCE_END_MS
    deepgram_connect_timeout_seconds: float = 10.0
    deepgram_ping_interval_seconds: float = 20.0
    deepgram_ping_timeout_seconds: float = 10.0
    deepgram_transcript_queue_max_size: int = DEFAULT_DEEPGRAM_TRANSCRIPT_QUEUE_MAX_SIZE
    deepgram_transcript_message_max_bytes: int = DEFAULT_DEEPGRAM_TRANSCRIPT_MESSAGE_MAX_BYTES
    deepgram_audio_queue_max_size: int = DEFAULT_DEEPGRAM_AUDIO_QUEUE_MAX_SIZE
    orchestrator_url: str | None = None
    orchestrator_api_key: SecretStr | None = None
    orchestrator_timeout_seconds: float = 20.0
    orchestrator_first_chunk_timeout_seconds: float = (
        DEFAULT_ORCHESTRATOR_FIRST_CHUNK_TIMEOUT_SECONDS
    )
    orchestrator_require_streaming_response: bool = (
        DEFAULT_ORCHESTRATOR_REQUIRE_STREAMING_RESPONSE
    )
    orchestrator_max_response_bytes: int = DEFAULT_ORCHESTRATOR_MAX_RESPONSE_BYTES
    # Per-project agent config forwarded to the orchestration LLM.
    orchestrator_system_prompt: str | None = None
    orchestrator_provider: str | None = None
    orchestrator_model: str | None = None
    google_live_api_key: SecretStr | None = None
    google_live_model: str = "gemini-2.5-flash-native-audio-preview-12-2025"
    google_live_voice: str | None = None
    google_live_connect_timeout_seconds: float = 10.0
    google_live_input_sample_rate_hz: int = 16000
    google_live_output_sample_rate_hz: int = 24000
    elevenlabs_api_key: SecretStr | None = None
    elevenlabs_voice_id: str | None = None
    elevenlabs_model_id: str = "eleven_flash_v2_5"
    elevenlabs_timeout_seconds: float = 30.0
    elevenlabs_max_audio_bytes: int = DEFAULT_ELEVENLABS_MAX_AUDIO_BYTES
    sarvam_api_key: SecretStr | None = None
    sarvam_speaker: str | None = None
    sarvam_model: str = "bulbul:v3"
    sarvam_target_language_code: str = "en-IN"
    sarvam_pace: float = 1.0
    sarvam_temperature: float = 0.6
    sarvam_streaming: bool = True
    sarvam_min_buffer_size: int = MIN_SARVAM_MIN_BUFFER_SIZE
    sarvam_enable_preprocessing: bool = True
    sarvam_api_base_url: str = "https://api.sarvam.ai"
    sarvam_timeout_seconds: float = 30.0
    sarvam_read_timeout_seconds: float = 10.0
    sarvam_max_audio_bytes: int = DEFAULT_ELEVENLABS_MAX_AUDIO_BYTES
    luxtts_tts_url: str | None = None
    luxtts_api_key: SecretStr | None = None
    luxtts_voice: str | None = None
    luxtts_model: str | None = None
    luxtts_timeout_seconds: float = 30.0
    luxtts_max_audio_bytes: int = DEFAULT_LUXTTS_MAX_AUDIO_BYTES
    luxtts_num_steps: int = DEFAULT_LUXTTS_NUM_STEPS
    luxtts_t_shift: float = DEFAULT_LUXTTS_T_SHIFT
    luxtts_speed: float = DEFAULT_LUXTTS_SPEED
    luxtts_min_first_chunk_chars: int = DEFAULT_LUXTTS_MIN_FIRST_CHUNK_CHARS
    luxtts_min_soft_chunk_chars: int = DEFAULT_LUXTTS_MIN_SOFT_CHUNK_CHARS
    luxtts_max_soft_chunk_chars: int = DEFAULT_LUXTTS_MAX_SOFT_CHUNK_CHARS

    # CPU-local speech stack: in-process streaming STT (sherpa-onnx Zipformer +
    # Silero VAD + optional smart-turn semantic endpointing) and in-process TTS
    # (Kokoro-82M). No network in the speech path; only the LLM leaves the box.
    # Paths are resolved relative to the process working directory unless absolute.
    sherpa_stt_model_dir: str = DEFAULT_SHERPA_STT_MODEL_DIR
    sherpa_stt_vad_model_path: str = DEFAULT_SHERPA_STT_VAD_MODEL_PATH
    sherpa_stt_num_threads: int = DEFAULT_SHERPA_STT_NUM_THREADS
    sherpa_stt_semantic_silence_ms: int = DEFAULT_SHERPA_STT_SEMANTIC_SILENCE_MS
    sherpa_stt_endpoint_silence_ms: int = DEFAULT_SHERPA_STT_ENDPOINT_SILENCE_MS
    sherpa_stt_hard_silence_ms: int = DEFAULT_SHERPA_STT_HARD_SILENCE_MS
    # Blank/unset disables semantic endpointing (silence-only commits).
    sherpa_stt_smart_turn_model_path: str | None = DEFAULT_SHERPA_STT_SMART_TURN_MODEL_PATH
    sherpa_stt_smart_turn_threshold: float = DEFAULT_SHERPA_STT_SMART_TURN_THRESHOLD
    kokoro_model_path: str = DEFAULT_KOKORO_MODEL_PATH
    kokoro_voices_path: str = DEFAULT_KOKORO_VOICES_PATH
    kokoro_voice: str = DEFAULT_KOKORO_VOICE
    kokoro_speed: float = DEFAULT_KOKORO_SPEED
    kokoro_lang: str = DEFAULT_KOKORO_LANG
    kokoro_min_first_chunk_chars: int = DEFAULT_KOKORO_MIN_FIRST_CHUNK_CHARS
    kokoro_min_soft_chunk_chars: int = DEFAULT_KOKORO_MIN_SOFT_CHUNK_CHARS
    kokoro_max_soft_chunk_chars: int = DEFAULT_KOKORO_MAX_SOFT_CHUNK_CHARS
    # Cap on the first synthesis unit inside each TTS call (0 disables); smaller
    # first units trade a little prosody for materially faster first audio.
    kokoro_first_unit_max_chars: int = DEFAULT_KOKORO_FIRST_UNIT_MAX_CHARS
    stream_status_callback_method: HTTPMethod = "POST"
    # By default an unverifiable Plivo media-stream status callback (one carrying a StreamID,
    # which signs in a form that cannot be reliably reconstructed) is acknowledged without
    # recording. Enable this to reject them with 403 instead (stricter, but only safe if your
    # Plivo signing form is reconstructable end-to-end).
    enforce_stream_status_signature: bool = False
    stream_noise_cancellation: bool = False
    stream_noise_cancellation_level: int = 85

    # Optional: mirror call lifecycle to the control plane (dashboard call index).
    control_plane_base_url: str | None = None
    control_plane_ingest_api_key: SecretStr | None = None

    @model_validator(mode="after")
    def validate_runtime_safety(self) -> Settings:
        _normalize_optional_string_field(self, "redis_url")
        _normalize_optional_string_field(self, "plivo_auth_id")
        _normalize_optional_string_field(self, "plivo_from_number")
        _normalize_optional_string_field(self, "orchestrator_url")
        _normalize_optional_string_field(self, "elevenlabs_voice_id")
        _normalize_required_string_field(self, "elevenlabs_model_id")
        _normalize_optional_string_field(self, "sarvam_speaker")
        _normalize_required_string_field(self, "sarvam_model")
        _normalize_required_string_field(self, "sarvam_target_language_code")
        _normalize_optional_secret_field(self, "sarvam_api_key")
        _normalize_optional_secret_field(self, "control_api_key")
        _normalize_optional_secret_field(self, "plivo_auth_token")
        _normalize_optional_secret_field(self, "stream_auth_token")
        _normalize_optional_secret_field(self, "deepgram_api_key")
        _normalize_optional_secret_field(self, "orchestrator_api_key")
        _normalize_optional_secret_field(self, "google_live_api_key")
        _normalize_required_string_field(self, "google_live_model")
        _normalize_optional_string_field(self, "google_live_voice")
        _normalize_optional_secret_field(self, "elevenlabs_api_key")
        _normalize_optional_string_field(self, "luxtts_tts_url")
        _normalize_optional_secret_field(self, "luxtts_api_key")
        _normalize_optional_string_field(self, "luxtts_voice")
        _normalize_optional_string_field(self, "luxtts_model")
        _normalize_optional_string_field(self, "sherpa_stt_smart_turn_model_path")
        _normalize_optional_string_field(self, "control_plane_base_url")
        _normalize_optional_secret_field(self, "control_plane_ingest_api_key")
        _normalize_optional_string_field(self, "orchestrator_system_prompt")
        _normalize_optional_string_field(self, "orchestrator_model")

        object.__setattr__(
            self,
            "public_base_url",
            _normalize_https_base_url("public_base_url", self.public_base_url),
        )
        object.__setattr__(
            self,
            "plivo_api_base_url",
            _normalize_https_base_url("plivo_api_base_url", self.plivo_api_base_url),
        )
        object.__setattr__(
            self,
            "sarvam_api_base_url",
            _normalize_https_base_url("sarvam_api_base_url", self.sarvam_api_base_url),
        )
        if self.orchestrator_url is not None:
            object.__setattr__(
                self,
                "orchestrator_url",
                _normalize_http_endpoint_url("orchestrator_url", self.orchestrator_url),
            )
        if self.luxtts_tts_url is not None:
            object.__setattr__(
                self,
                "luxtts_tts_url",
                _normalize_http_endpoint_url("luxtts_tts_url", self.luxtts_tts_url),
            )

        using_google_live = self.llm_provider == "google_live"
        local_providers = {
            "stt": self.stt_provider == "local",
            "llm": self.llm_provider == "local",
            "tts": self.tts_provider == "local",
        }
        if self.environment == "production":
            if self.session_backend == "memory":
                raise ValueError("production requires session_backend=redis.")
            if any(local_providers.values()) and not using_google_live:
                names = ", ".join(name for name, is_local in local_providers.items() if is_local)
                raise ValueError(f"production cannot use local provider backends: {names}.")
            if self.plivo_auth_token is None:
                raise ValueError("production requires plivo_auth_token for webhook validation.")
            _validate_secret_not_blank("plivo_auth_token", self.plivo_auth_token)
            if self.control_api_key is None:
                raise ValueError("production requires control_api_key for outbound call control.")
            if _is_blank(self.plivo_auth_id):
                raise ValueError("production requires plivo_auth_id for outbound calls.")
            if _is_blank(self.plivo_from_number):
                raise ValueError("production requires plivo_from_number for outbound calls.")
            if not _is_e164_phone_number(self.plivo_from_number):
                raise ValueError("plivo_from_number must be an E.164 phone number.")
            if self.stream_auth_token is None:
                raise ValueError("production requires stream_auth_token for WebSocket validation.")
            if (
                self.orchestrator_url
                and urlsplit(self.orchestrator_url).scheme != "https"
                and not _is_loopback_http_url(self.orchestrator_url)
            ):
                raise ValueError(
                    "production requires orchestrator_url to use https "
                    "unless it points to a loopback sidecar."
                )
            if (
                self.tts_provider == "luxtts"
                and self.luxtts_tts_url
                and urlsplit(self.luxtts_tts_url).scheme != "https"
                and not _is_loopback_http_url(self.luxtts_tts_url)
            ):
                raise ValueError(
                    "production requires luxtts_tts_url to use https "
                    "unless it points to a loopback sidecar."
                )
            if (
                self.tts_provider == "luxtts"
                and self.luxtts_tts_url
                and not _is_loopback_http_url(self.luxtts_tts_url)
                and self.luxtts_api_key is None
            ):
                raise ValueError("production requires luxtts_api_key for remote LuxTTS.")
            _validate_internal_secret_strength("control_api_key", self.control_api_key)
            _validate_internal_secret_strength("stream_auth_token", self.stream_auth_token)

        if self.turn_timeout_seconds <= 0:
            raise ValueError("turn_timeout_seconds must be positive.")
        if self.fallback_timeout_seconds <= 0:
            raise ValueError("fallback_timeout_seconds must be positive.")
        if self.audio_ingest_timeout_seconds <= 0:
            raise ValueError("audio_ingest_timeout_seconds must be positive.")
        if self.plivo_send_timeout_seconds <= 0:
            raise ValueError("plivo_send_timeout_seconds must be positive.")
        if self.provider_close_timeout_seconds <= 0:
            raise ValueError("provider_close_timeout_seconds must be positive.")
        if self.provider_retry_max_attempts < 1:
            raise ValueError("provider_retry_max_attempts must be at least 1.")
        if self.provider_retry_backoff_seconds <= 0:
            raise ValueError("provider_retry_backoff_seconds must be positive.")
        if self.provider_retry_max_backoff_seconds < self.provider_retry_backoff_seconds:
            raise ValueError(
                "provider_retry_max_backoff_seconds must be >= provider_retry_backoff_seconds."
            )
        if self.max_transcript_chars <= 0:
            raise ValueError("max_transcript_chars must be positive.")
        if self.max_reply_chars <= 0:
            raise ValueError("max_reply_chars must be positive.")
        if self.max_turns_per_call < 0:
            raise ValueError("max_turns_per_call must be non-negative.")
        if self.transcript_persist_interval_seconds < 0:
            raise ValueError("transcript_persist_interval_seconds must be non-negative.")
        if self.control_rate_limit_per_minute <= 0:
            raise ValueError("control_rate_limit_per_minute must be positive.")
        if self.max_play_audio_bytes < 2 or self.max_play_audio_bytes % 2:
            raise ValueError("max_play_audio_bytes must be an even integer of at least 2.")
        if self.pre_response_ack_text is not None:
            clean_pre_response_ack_text = self.pre_response_ack_text.strip()
            if not clean_pre_response_ack_text:
                raise ValueError("pre_response_ack_text must not be blank.")
            if len(clean_pre_response_ack_text) > MAX_PRE_RESPONSE_ACK_CHARS:
                raise ValueError(
                    f"pre_response_ack_text must be at most "
                    f"{MAX_PRE_RESPONSE_ACK_CHARS} characters."
                )
        if self.pre_response_ack_delay_ms < 0:
            raise ValueError("pre_response_ack_delay_ms must be non-negative.")
        if self.local_end_of_turn_silence_ms < 0:
            raise ValueError("local_end_of_turn_silence_ms must be non-negative.")
        if self.fast_final_end_of_turn_silence_ms < 0:
            raise ValueError("fast_final_end_of_turn_silence_ms must be non-negative.")
        if self.interim_transcript_stability_ms < 0:
            raise ValueError("interim_transcript_stability_ms must be non-negative.")
        if self.speculative_response_stability_ms < 0:
            raise ValueError("speculative_response_stability_ms must be non-negative.")
        if self.speculative_response_min_chars < 0:
            raise ValueError("speculative_response_min_chars must be non-negative.")
        if self.luxtts_min_first_chunk_chars < 0:
            raise ValueError("luxtts_min_first_chunk_chars must be non-negative.")
        if self.luxtts_min_soft_chunk_chars <= 0:
            raise ValueError("luxtts_min_soft_chunk_chars must be positive.")
        if self.luxtts_max_soft_chunk_chars < self.luxtts_min_soft_chunk_chars:
            raise ValueError(
                "luxtts_max_soft_chunk_chars must be >= luxtts_min_soft_chunk_chars."
            )
        if self.barge_in_min_frames < 1:
            raise ValueError("barge_in_min_frames must be at least 1.")
        if self.barge_in_energy_threshold < 0:
            raise ValueError("barge_in_energy_threshold must be non-negative.")
        if self.media_start_timeout_seconds <= 0:
            raise ValueError("media_start_timeout_seconds must be positive.")
        if self.stream_token_ttl_seconds <= 0:
            raise ValueError("stream_token_ttl_seconds must be positive.")
        if self.webhook_nonce_ttl_seconds <= 0:
            raise ValueError("webhook_nonce_ttl_seconds must be positive.")
        if self.call_session_ttl_seconds <= 0:
            raise ValueError("call_session_ttl_seconds must be positive.")
        if self.call_event_stream_maxlen <= 0:
            raise ValueError("call_event_stream_maxlen must be positive.")
        if self.readiness_check_timeout_seconds <= 0:
            raise ValueError("readiness_check_timeout_seconds must be positive.")
        if self.shutdown_grace_seconds < 0:
            raise ValueError("shutdown_grace_seconds must be non-negative.")
        if self.log_level.strip().upper() not in {
            "DEBUG",
            "INFO",
            "WARNING",
            "ERROR",
            "CRITICAL",
        }:
            raise ValueError(
                "log_level must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL."
            )
        if self.callback_max_body_bytes <= 0:
            raise ValueError("callback_max_body_bytes must be positive.")
        if self.control_max_body_bytes <= 0:
            raise ValueError("control_max_body_bytes must be positive.")
        if self.media_message_max_bytes <= 0:
            raise ValueError("media_message_max_bytes must be positive.")
        if self.plivo_api_timeout_seconds <= 0:
            raise ValueError("plivo_api_timeout_seconds must be positive.")
        if self.plivo_api_max_response_bytes <= 0:
            raise ValueError("plivo_api_max_response_bytes must be positive.")
        if self.orchestrator_timeout_seconds <= 0:
            raise ValueError("orchestrator_timeout_seconds must be positive.")
        if self.orchestrator_first_chunk_timeout_seconds <= 0:
            raise ValueError("orchestrator_first_chunk_timeout_seconds must be positive.")
        if self.orchestrator_max_response_bytes <= 0:
            raise ValueError("orchestrator_max_response_bytes must be positive.")
        if self.elevenlabs_timeout_seconds <= 0:
            raise ValueError("elevenlabs_timeout_seconds must be positive.")
        if self.elevenlabs_max_audio_bytes <= 0:
            raise ValueError("elevenlabs_max_audio_bytes must be positive.")
        if self.luxtts_timeout_seconds <= 0:
            raise ValueError("luxtts_timeout_seconds must be positive.")
        if self.luxtts_max_audio_bytes <= 0:
            raise ValueError("luxtts_max_audio_bytes must be positive.")
        if self.luxtts_num_steps <= 0:
            raise ValueError("luxtts_num_steps must be positive.")
        if self.luxtts_t_shift <= 0:
            raise ValueError("luxtts_t_shift must be positive.")
        if self.luxtts_speed <= 0:
            raise ValueError("luxtts_speed must be positive.")
        if self.deepgram_endpointing_ms <= 0:
            raise ValueError("deepgram_endpointing_ms must be positive.")
        if self.deepgram_utterance_end_ms < DEFAULT_DEEPGRAM_UTTERANCE_END_MS:
            raise ValueError("deepgram_utterance_end_ms must be at least 1000.")
        if self.deepgram_connect_timeout_seconds <= 0:
            raise ValueError("deepgram_connect_timeout_seconds must be positive.")
        if self.deepgram_ping_interval_seconds <= 0:
            raise ValueError("deepgram_ping_interval_seconds must be positive.")
        if self.deepgram_ping_timeout_seconds <= 0:
            raise ValueError("deepgram_ping_timeout_seconds must be positive.")
        if self.deepgram_transcript_queue_max_size <= 0:
            raise ValueError("deepgram_transcript_queue_max_size must be positive.")
        if self.deepgram_transcript_message_max_bytes <= 0:
            raise ValueError("deepgram_transcript_message_max_bytes must be positive.")
        if self.deepgram_audio_queue_max_size <= 0:
            raise ValueError("deepgram_audio_queue_max_size must be positive.")
        if self.stream_timeout_seconds <= 0:
            raise ValueError("stream_timeout_seconds must be positive.")
        if not 60 <= self.stream_noise_cancellation_level <= 100:
            raise ValueError("stream_noise_cancellation_level must be between 60 and 100.")
        if self.outbound_hangup_on_ring_seconds <= 0:
            raise ValueError("outbound_hangup_on_ring_seconds must be positive.")
        if self.outbound_time_limit_seconds <= 0:
            raise ValueError("outbound_time_limit_seconds must be positive.")
        if self.session_backend == "redis" and _is_blank(self.redis_url):
            raise ValueError("redis_url is required when session_backend=redis.")
        if self.session_backend == "redis" and self.redis_url is not None:
            object.__setattr__(
                self,
                "redis_url",
                _normalize_redis_url(self.redis_url),
            )
        if self.google_live_connect_timeout_seconds <= 0:
            raise ValueError("google_live_connect_timeout_seconds must be positive.")
        if self.google_live_input_sample_rate_hz <= 0:
            raise ValueError("google_live_input_sample_rate_hz must be positive.")
        if self.google_live_output_sample_rate_hz <= 0:
            raise ValueError("google_live_output_sample_rate_hz must be positive.")
        if self.llm_provider == "google_live":
            if self.google_live_api_key is None:
                raise ValueError("google_live_api_key is required when llm_provider=google_live.")
            _validate_secret_not_blank("google_live_api_key", self.google_live_api_key)
            if _is_blank(self.google_live_model):
                raise ValueError("google_live_model is required when llm_provider=google_live.")
        if self.stt_provider == "deepgram" and not using_google_live:
            if self.deepgram_api_key is None:
                raise ValueError("deepgram_api_key is required when stt_provider=deepgram.")
            _validate_secret_not_blank("deepgram_api_key", self.deepgram_api_key)
        if self.llm_provider == "orchestrator" and _is_blank(self.orchestrator_url):
            raise ValueError("orchestrator_url is required when llm_provider=orchestrator.")
        if self.orchestrator_api_key is not None:
            _validate_secret_not_blank("orchestrator_api_key", self.orchestrator_api_key)
        if self.tts_provider == "elevenlabs" and not using_google_live:
            if self.elevenlabs_api_key is None:
                raise ValueError("elevenlabs_api_key is required when tts_provider=elevenlabs.")
            _validate_secret_not_blank("elevenlabs_api_key", self.elevenlabs_api_key)
            if _is_blank(self.elevenlabs_voice_id):
                raise ValueError("elevenlabs_voice_id is required when tts_provider=elevenlabs.")
            if _is_blank(self.elevenlabs_model_id):
                raise ValueError("elevenlabs_model_id is required when tts_provider=elevenlabs.")
        if self.tts_provider == "sarvam" and not using_google_live:
            if self.sarvam_api_key is None:
                raise ValueError("sarvam_api_key is required when tts_provider=sarvam.")
            _validate_secret_not_blank("sarvam_api_key", self.sarvam_api_key)
            if _is_blank(self.sarvam_model):
                raise ValueError("sarvam_model is required when tts_provider=sarvam.")
            if _is_blank(self.sarvam_target_language_code):
                raise ValueError(
                    "sarvam_target_language_code is required when tts_provider=sarvam."
                )
        if self.tts_provider == "luxtts" and not using_google_live:
            if _is_blank(self.luxtts_tts_url):
                raise ValueError("luxtts_tts_url is required when tts_provider=luxtts.")
            if self.luxtts_api_key is not None:
                _validate_secret_not_blank("luxtts_api_key", self.luxtts_api_key)
        if self.stt_provider == "sherpa" and not using_google_live:
            if _is_blank(self.sherpa_stt_model_dir):
                raise ValueError("sherpa_stt_model_dir is required when stt_provider=sherpa.")
            if _is_blank(self.sherpa_stt_vad_model_path):
                raise ValueError(
                    "sherpa_stt_vad_model_path is required when stt_provider=sherpa."
                )
        if self.sherpa_stt_num_threads <= 0:
            raise ValueError("sherpa_stt_num_threads must be positive.")
        if self.sherpa_stt_semantic_silence_ms <= 0:
            raise ValueError("sherpa_stt_semantic_silence_ms must be positive.")
        if self.sherpa_stt_endpoint_silence_ms < self.sherpa_stt_semantic_silence_ms:
            raise ValueError(
                "sherpa_stt_endpoint_silence_ms must be >= sherpa_stt_semantic_silence_ms."
            )
        if self.sherpa_stt_hard_silence_ms < self.sherpa_stt_endpoint_silence_ms:
            raise ValueError(
                "sherpa_stt_hard_silence_ms must be >= sherpa_stt_endpoint_silence_ms."
            )
        if not 0.0 < self.sherpa_stt_smart_turn_threshold < 1.0:
            raise ValueError(
                "sherpa_stt_smart_turn_threshold must be between 0 and 1 (exclusive)."
            )
        if self.tts_provider == "kokoro" and not using_google_live:
            if _is_blank(self.kokoro_model_path):
                raise ValueError("kokoro_model_path is required when tts_provider=kokoro.")
            if _is_blank(self.kokoro_voices_path):
                raise ValueError("kokoro_voices_path is required when tts_provider=kokoro.")
            if _is_blank(self.kokoro_voice):
                raise ValueError("kokoro_voice is required when tts_provider=kokoro.")
            if _is_blank(self.kokoro_lang):
                raise ValueError("kokoro_lang is required when tts_provider=kokoro.")
        if self.kokoro_speed <= 0:
            raise ValueError("kokoro_speed must be positive.")
        if self.kokoro_min_first_chunk_chars < 0:
            raise ValueError("kokoro_min_first_chunk_chars must be non-negative.")
        if self.kokoro_min_soft_chunk_chars <= 0:
            raise ValueError("kokoro_min_soft_chunk_chars must be positive.")
        if self.kokoro_max_soft_chunk_chars < self.kokoro_min_soft_chunk_chars:
            raise ValueError(
                "kokoro_max_soft_chunk_chars must be >= kokoro_min_soft_chunk_chars."
            )
        if self.kokoro_first_unit_max_chars < 0:
            raise ValueError("kokoro_first_unit_max_chars must be non-negative.")
        if self.sarvam_pace <= 0:
            raise ValueError("sarvam_pace must be positive.")
        if self.sarvam_temperature <= 0:
            raise ValueError("sarvam_temperature must be positive.")
        if self.sarvam_min_buffer_size < MIN_SARVAM_MIN_BUFFER_SIZE:
            raise ValueError(
                f"sarvam_min_buffer_size must be at least {MIN_SARVAM_MIN_BUFFER_SIZE}."
            )
        if self.sarvam_timeout_seconds <= 0:
            raise ValueError("sarvam_timeout_seconds must be positive.")
        if self.sarvam_read_timeout_seconds <= 0:
            raise ValueError("sarvam_read_timeout_seconds must be positive.")
        if self.redis_socket_timeout_seconds <= 0:
            raise ValueError("redis_socket_timeout_seconds must be positive.")
        if self.redis_connect_timeout_seconds <= 0:
            raise ValueError("redis_connect_timeout_seconds must be positive.")
        if self.redis_health_check_interval_seconds <= 0:
            raise ValueError("redis_health_check_interval_seconds must be positive.")
        if self.redis_max_connections <= 0:
            raise ValueError("redis_max_connections must be positive.")
        if self.sarvam_max_audio_bytes <= 0:
            raise ValueError("sarvam_max_audio_bytes must be positive.")

        return self

    @property
    def media_websocket_url(self) -> str:
        return self.public_base_url.replace("https://", "wss://", 1) + "/plivo/media"

    @property
    def status_callback_url(self) -> str:
        return self.public_base_url + "/plivo/status"

    @property
    def answer_callback_url(self) -> str:
        return self.public_base_url + "/plivo/answer"

    @property
    def should_validate_plivo_signature(self) -> bool:
        return self.plivo_auth_token is not None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def _validate_internal_secret_strength(name: str, secret: SecretStr | None) -> None:
    if secret is None:
        return
    value = secret.get_secret_value().strip()
    if not value:
        raise ValueError(f"{name} must not be blank.")
    if len(value) < MIN_INTERNAL_SECRET_LENGTH:
        raise ValueError(f"{name} must be at least {MIN_INTERNAL_SECRET_LENGTH} characters.")


def _validate_secret_not_blank(name: str, secret: SecretStr) -> None:
    if not secret.get_secret_value().strip():
        raise ValueError(f"{name} must not be blank.")


def _normalize_optional_string_field(settings: Settings, name: str) -> None:
    value = getattr(settings, name)
    if isinstance(value, str):
        normalized_value = value.strip()
        object.__setattr__(settings, name, normalized_value if normalized_value else None)


def _normalize_https_base_url(name: str, value: str) -> str:
    normalized_value = value.strip().rstrip("/")
    parsed_url = urlsplit(normalized_value)
    if parsed_url.scheme != "https" or parsed_url.hostname is None:
        raise ValueError(f"{name} must be a valid https URL with a host.")
    if parsed_url.username is not None or parsed_url.password is not None:
        raise ValueError(f"{name} must not include credentials.")
    if parsed_url.query or parsed_url.fragment:
        raise ValueError(f"{name} must not include a query string or fragment.")
    return normalized_value


def _normalize_http_endpoint_url(name: str, value: str) -> str:
    normalized_value = value.strip().rstrip("/")
    parsed_url = urlsplit(normalized_value)
    if parsed_url.scheme not in {"http", "https"}:
        raise ValueError(f"{name} must use http or https.")
    if parsed_url.hostname is None:
        raise ValueError(f"{name} must be a valid http or https URL with a host.")
    if parsed_url.username is not None or parsed_url.password is not None:
        raise ValueError(f"{name} must not include credentials.")
    if parsed_url.query or parsed_url.fragment:
        raise ValueError(f"{name} must not include a query string or fragment.")
    return normalized_value


def _is_loopback_http_url(value: str) -> bool:
    parsed_url = urlsplit(value)
    if parsed_url.scheme != "http" or parsed_url.hostname is None:
        return False
    hostname = parsed_url.hostname.lower()
    if hostname == "localhost":
        return True
    try:
        return ip_address(hostname).is_loopback
    except ValueError:
        return False


def _normalize_redis_url(value: str) -> str:
    normalized_value = value.strip()
    parsed_url = urlsplit(normalized_value)
    if parsed_url.scheme not in {"redis", "rediss", "unix"}:
        raise ValueError("redis_url must use redis, rediss, or unix scheme.")
    if parsed_url.scheme in {"redis", "rediss"} and parsed_url.hostname is None:
        raise ValueError("redis_url must include a host when using redis or rediss.")
    if parsed_url.scheme == "unix" and not parsed_url.path:
        raise ValueError("redis_url must include a socket path when using unix.")
    try:
        _ = parsed_url.port
    except ValueError as exc:
        raise ValueError("redis_url has invalid port.") from exc
    return normalized_value


def _normalize_required_string_field(settings: Settings, name: str) -> None:
    value = getattr(settings, name)
    if isinstance(value, str):
        object.__setattr__(settings, name, value.strip())


def _normalize_optional_secret_field(settings: Settings, name: str) -> None:
    value = getattr(settings, name)
    if isinstance(value, SecretStr):
        object.__setattr__(settings, name, SecretStr(value.get_secret_value().strip()))


def _is_blank(value: str | None) -> bool:
    return value is None or not value.strip()


def _is_e164_phone_number(value: str | None) -> bool:
    return isinstance(value, str) and E164_PHONE_RE.fullmatch(value) is not None
