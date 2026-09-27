from typing import Any

import pytest

import voice_agent.providers.factory as factory_module
from voice_agent.config.settings import Settings
from voice_agent.providers.deepgram import DeepgramStreamingSpeechToText
from voice_agent.providers.elevenlabs import ElevenLabsTextToSpeech
from voice_agent.providers.factory import build_media_session_runner, prewarm_cached_tts_audio
from voice_agent.providers.google_live import GoogleLiveMediaSessionRunner, GoogleLiveRealtimeClient
from voice_agent.providers.kokoro_tts import KokoroTextToSpeech
from voice_agent.providers.local import (
    LocalLLMClient,
    LocalSpeechToText,
    LocalTextToSpeech,
)
from voice_agent.providers.luxtts import LuxTTSHttpTextToSpeech
from voice_agent.providers.orchestrator import OrchestratorLLMClient
from voice_agent.providers.sarvam import SarvamStreamingTextToSpeech, SarvamTextToSpeech
from voice_agent.providers.sherpa_stt import SherpaStreamingSpeechToText
from voice_agent.providers.smart_turn import SmartTurnSemanticEndpointer
from voice_agent.workflow.audio_cache import InMemoryAudioCache


class CapturingTransport:
    async def send_json(self, message: dict[str, Any]) -> None:
        del message


@pytest.mark.anyio
async def test_factory_builds_production_runner_with_real_provider_adapters() -> None:
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
        tts_provider="elevenlabs",
        elevenlabs_api_key="eleven-secret",
        elevenlabs_voice_id="voice-1",
        control_api_key="control-secret-0123456789abcdef012345",
        plivo_auth_id="plivo-auth-id",
        plivo_auth_token="plivo-auth-token",
        plivo_from_number="+14155550100",
        stream_auth_token="stream-secret-0123456789abcdef012345",
        deepgram_endpointing_ms=250,
        deepgram_utterance_end_ms=1200,
        deepgram_connect_timeout_seconds=4.5,
        deepgram_ping_interval_seconds=6.5,
        deepgram_ping_timeout_seconds=7.5,
        deepgram_transcript_queue_max_size=7,
        deepgram_transcript_message_max_bytes=8192,
        deepgram_audio_queue_max_size=17,
        max_transcript_chars=2048,
        max_reply_chars=768,
        max_play_audio_bytes=3200,
        pre_response_ack_text="One moment.",
        pre_response_ack_delay_ms=250,
        local_end_of_turn_silence_ms=300,
        fast_final_end_of_turn_silence_ms=180,
        interim_transcript_stability_ms=175,
        interim_local_silence_commit_enabled=True,
        speculative_response_prewarm=False,
        speculative_response_stability_ms=25,
        speculative_response_min_chars=14,
        orchestrator_timeout_seconds=4.5,
        orchestrator_first_chunk_timeout_seconds=2.5,
        orchestrator_max_response_bytes=8192,
        elevenlabs_timeout_seconds=5.5,
        elevenlabs_max_audio_bytes=262144,
    )

    runner = await build_media_session_runner(
        settings=settings,
        call_id="call-1",
        stream_id="stream-1",
        transport=CapturingTransport(),
    )

    assert runner.call_id == "call-1"
    assert runner.stream_id == "stream-1"
    assert isinstance(runner._stt, DeepgramStreamingSpeechToText)
    assert runner._stt._endpointing_ms == 250
    assert runner._stt._utterance_end_ms == 1200
    assert runner._stt._connect_timeout_seconds == 4.5
    assert runner._stt._ping_interval_seconds == 6.5
    assert runner._stt._ping_timeout_seconds == 7.5
    assert runner._stt._transcript_queue_max_size == 7
    assert runner._stt._transcript_message_max_bytes == 8192
    assert runner._stt._audio_queue_max_size == 17
    assert runner._max_transcript_chars == 2048
    assert runner._max_reply_chars == 768
    assert runner._max_play_audio_bytes == 3200
    assert runner._pre_response_ack_text == "One moment."
    assert runner._pre_response_ack_delay_seconds == 0.25
    assert runner._audio_cache is not None
    assert runner._audio_cache_context is not None
    assert runner._audio_cache_context.tts_provider == "elevenlabs"
    assert runner._audio_cache_context.voice == "voice-1"
    assert runner._audio_cache_context.model == "eleven_flash_v2_5"
    assert runner._turn_policy.local_end_of_turn_silence_seconds == 0.3
    assert runner._turn_policy.fast_final_end_of_turn_silence_seconds == 0.18
    assert runner._turn_policy.interim_transcript_stability_seconds == 0.175
    assert runner._turn_policy.interim_local_silence_commit_enabled
    assert not runner._speculative_response_prewarm
    assert runner._speculative_response_stability_seconds == 0.025
    assert runner._speculative_response_min_chars == 14
    assert isinstance(runner._llm, OrchestratorLLMClient)
    assert runner._llm._timeout_seconds == 4.5
    assert runner._llm._first_chunk_timeout_seconds == 2.5
    assert runner._llm._max_response_bytes == 8192
    assert runner._llm._require_streaming_response
    assert isinstance(runner._tts, ElevenLabsTextToSpeech)
    assert runner._tts._timeout_seconds == 5.5
    assert runner._tts._max_audio_bytes == 262144
    await runner.close()


@pytest.mark.anyio
async def test_factory_builds_mixed_local_stt_orchestrator_llm_local_tts() -> None:
    # Local-mode mix used for testing the orchestrator brain (e.g. a Claude-CLI
    # backed /voice/respond) without paid STT/TTS providers.
    settings = Settings(
        environment="local",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="orchestrator",
        orchestrator_url="http://127.0.0.1:8090/voice/respond",
        tts_provider="local",
        turn_timeout_seconds=30.0,
    )

    runner = await build_media_session_runner(
        settings=settings,
        call_id="call-mix",
        stream_id="stream-mix",
        transport=CapturingTransport(),
    )

    assert isinstance(runner._stt, LocalSpeechToText)
    assert isinstance(runner._llm, OrchestratorLLMClient)
    assert runner._llm._require_streaming_response
    assert isinstance(runner._tts, LocalTextToSpeech)
    await runner.close()


@pytest.mark.anyio
async def test_factory_prewarmed_greeting_uses_same_cache_as_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = InMemoryAudioCache(max_entries=8, max_bytes=128_000)
    monkeypatch.setattr(factory_module, "_AUDIO_CACHE", cache)
    settings = Settings(
        environment="local",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="local",
        tts_provider="local",
    )

    warmed = await prewarm_cached_tts_audio(
        settings=settings,
        text="Hi, this is VoicePilot.",
        purpose="greeting",
    )
    runner = await build_media_session_runner(
        settings=settings,
        call_id="call-prewarm",
        stream_id="stream-prewarm",
        transport=CapturingTransport(),
        greeting_override="Hi, this is VoicePilot.",
    )

    assert warmed
    assert runner._audio_cache is cache
    assert runner._cached_greeting_audio("Hi, this is VoicePilot.") is not None
    assert cache.hit_count == 1
    await runner.close()


@pytest.mark.anyio
async def test_factory_builds_google_live_realtime_runner_without_stt_or_tts() -> None:
    settings = Settings(
        environment="test",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="orchestrator",
        orchestrator_url="https://orchestrator.example.com/respond",
        tts_provider="local",
        google_live_api_key="google-secret",
    )

    runner = await build_media_session_runner(
        settings=settings,
        call_id="call-live",
        stream_id="stream-live",
        transport=CapturingTransport(),
        llm_provider_override="google_live",
        llm_model_override="gemini-2.5-flash-native-audio-preview-12-2025",
        llm_system_prompt_override="You are Acme Dental.",
        greeting_override="Hello from Google Live.",
    )

    assert isinstance(runner, GoogleLiveMediaSessionRunner)
    assert isinstance(runner._client, GoogleLiveRealtimeClient)
    assert runner._client._model == "gemini-2.5-flash-native-audio-preview-12-2025"
    assert runner._system_instruction == "You are Acme Dental."
    assert runner._greeting == "Hello from Google Live."
    await runner.close()


@pytest.mark.anyio
async def test_factory_adds_handoff_roster_to_orchestrator_system_prompt() -> None:
    settings = Settings(
        environment="local",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="orchestrator",
        orchestrator_url="http://127.0.0.1:8090/voice/respond",
        tts_provider="local",
    )

    runner = await build_media_session_runner(
        settings=settings,
        call_id="call-handoff",
        stream_id="stream-handoff",
        transport=CapturingTransport(),
        llm_system_prompt_override="You are the hospital receptionist.",
        behavior_override={
            "handoffs": [
                {
                    "target_agent_name": "Appointment Manager",
                    "condition": "Appointment booking, rescheduling, or cancellation",
                    "instruction": "Use this specialist for scheduling work.",
                    "target": {
                        "system_prompt": "You manage hospital appointment slots.",
                        "llm": {"provider": "google", "model": "gemini-3.1-pro-preview"},
                    },
                }
            ]
        },
    )

    assert isinstance(runner._llm, OrchestratorLLMClient)
    assert runner._llm._system_prompt is not None
    assert "You are the hospital receptionist." in runner._llm._system_prompt
    assert "Available handoff targets" in runner._llm._system_prompt
    assert "Appointment Manager" in runner._llm._system_prompt
    assert "gemini-3.1-pro-preview" in runner._llm._system_prompt
    await runner.close()


@pytest.mark.anyio
async def test_factory_wires_handoff_targets_to_runtime_llm_factory() -> None:
    settings = Settings(
        environment="local",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="orchestrator",
        orchestrator_url="http://127.0.0.1:8090/voice/respond",
        tts_provider="local",
    )

    runner = await build_media_session_runner(
        settings=settings,
        call_id="call-handoff-runtime",
        stream_id="stream-handoff-runtime",
        transport=CapturingTransport(),
        llm_system_prompt_override="You are the hospital receptionist.",
        behavior_override={
            "handoffs": [
                {
                    "target_agent_config_id": "11111111-1111-1111-1111-111111111111",
                    "target_agent_name": "Appointment Manager",
                    "condition": "Appointment booking, rescheduling, or cancellation",
                    "instruction": "Use this specialist for scheduling work.",
                    "target": {
                        "system_prompt": "You manage hospital appointment slots.",
                        "llm": {
                            "provider": "google",
                            "model": "gemini-3.1-pro-preview",
                            "temperature": 0.2,
                        },
                    },
                }
            ]
        },
    )

    assert runner._handoff_targets
    target = runner._handoff_targets[0]
    assert target.target_id == "11111111-1111-1111-1111-111111111111"
    assert runner._handoff_llm_factory is not None
    handoff_llm = runner._handoff_llm_factory(
        target,
        "user: I need to book an appointment tomorrow",
    )

    assert isinstance(handoff_llm, OrchestratorLLMClient)
    assert handoff_llm._provider == "google"
    assert handoff_llm._model == "gemini-3.1-pro-preview"
    assert handoff_llm._temperature == 0.2
    assert handoff_llm._system_prompt is not None
    assert "You manage hospital appointment slots." in handoff_llm._system_prompt
    assert "Use this specialist for scheduling work." in handoff_llm._system_prompt
    assert "user: I need to book an appointment tomorrow" in handoff_llm._system_prompt
    await handoff_llm.close()
    await runner.close()


@pytest.mark.anyio
async def test_factory_adds_handoff_roster_to_google_live_system_prompt() -> None:
    settings = Settings(
        environment="test",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="orchestrator",
        orchestrator_url="https://orchestrator.example.com/respond",
        tts_provider="local",
        google_live_api_key="google-secret",
    )

    runner = await build_media_session_runner(
        settings=settings,
        call_id="call-live-handoff",
        stream_id="stream-live-handoff",
        transport=CapturingTransport(),
        llm_provider_override="google_live",
        llm_model_override="gemini-3.1-flash-live-preview",
        llm_system_prompt_override="You answer basic hospital questions.",
        behavior_override={
            "handoffs": [
                {
                    "target_agent_name": "Appointment Manager",
                    "condition": "Appointment booking",
                    "target": {"system_prompt": "You manage appointments."},
                }
            ]
        },
    )

    assert isinstance(runner, GoogleLiveMediaSessionRunner)
    assert runner._system_instruction is not None
    assert "You answer basic hospital questions." in runner._system_instruction
    assert "Available handoff targets" in runner._system_instruction
    assert "Appointment Manager" in runner._system_instruction
    await runner.close()


@pytest.mark.anyio
async def test_factory_wires_google_live_handoff_targets_to_realtime_client_factory() -> None:
    settings = Settings(
        environment="test",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="orchestrator",
        orchestrator_url="https://orchestrator.example.com/respond",
        tts_provider="local",
        google_live_api_key="google-secret",
        google_live_voice="Aoede",
    )

    runner = await build_media_session_runner(
        settings=settings,
        call_id="call-live-runtime-handoff",
        stream_id="stream-live-runtime-handoff",
        transport=CapturingTransport(),
        llm_provider_override="google_live",
        llm_model_override="gemini-3.1-flash-live-preview",
        llm_system_prompt_override="You answer basic hospital questions.",
        behavior_override={
            "handoffs": [
                {
                    "target_agent_config_id": "22222222-2222-2222-2222-222222222222",
                    "target_agent_name": "Appointment Manager",
                    "condition": "Appointment booking",
                    "instruction": "Handle scheduling.",
                    "target": {
                        "system_prompt": "You manage appointments.",
                        "llm": {
                            "provider": "google_live",
                            "model": "gemini-2.5-flash-native-audio-preview-12-2025",
                            "temperature": 0.3,
                        },
                    },
                }
            ]
        },
    )

    assert isinstance(runner, GoogleLiveMediaSessionRunner)
    assert runner._handoff_targets
    assert runner._handoff_client_factory is not None
    target_client, system_instruction = runner._handoff_client_factory(
        runner._handoff_targets[0],
        "user: I need an appointment.",
    )

    assert isinstance(target_client, GoogleLiveRealtimeClient)
    assert target_client._model == "gemini-2.5-flash-native-audio-preview-12-2025"
    assert target_client._voice == "Aoede"
    assert target_client._temperature == 0.3
    assert system_instruction is not None
    assert "You manage appointments." in system_instruction
    assert "Handle scheduling." in system_instruction
    assert "user: I need an appointment." in system_instruction
    await target_client.close()
    await runner.close()


@pytest.mark.anyio
async def test_factory_keeps_pre_response_ack_disabled_even_when_behavior_requests_one() -> None:
    settings = Settings(
        environment="local",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="local",
        tts_provider="local",
        pre_response_ack_text=None,
    )

    runner = await build_media_session_runner(
        settings=settings,
        call_id="call-no-ack",
        stream_id="stream-no-ack",
        transport=CapturingTransport(),
        behavior_override={"pre_response_ack_text": "Sure.", "pre_response_ack_delay_ms": 0},
    )

    assert runner._pre_response_ack_text is None
    await runner.close()


def _sarvam_settings(**overrides: object) -> Settings:
    base: dict[str, object] = dict(
        environment="local",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="deepgram",
        deepgram_api_key="deepgram-secret",
        llm_provider="orchestrator",
        orchestrator_url="https://orchestrator.example.com/respond",
        tts_provider="sarvam",
        sarvam_api_key="sarvam-secret",
        sarvam_speaker="anand",
        sarvam_model="bulbul:v3",
        sarvam_target_language_code="en-IN",
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_factory_builds_streaming_sarvam_tts_by_default() -> None:
    runner = await build_media_session_runner(
        settings=_sarvam_settings(sarvam_read_timeout_seconds=6.5),
        call_id="call-sarvam",
        stream_id="stream-sarvam",
        transport=CapturingTransport(),
    )

    assert isinstance(runner._tts, SarvamStreamingTextToSpeech)
    assert runner._tts._read_timeout_seconds == 6.5
    assert runner._tts._min_buffer_size == 50
    await runner.close()


@pytest.mark.anyio
async def test_factory_builds_rest_sarvam_tts_when_streaming_disabled() -> None:
    runner = await build_media_session_runner(
        settings=_sarvam_settings(sarvam_streaming=False),
        call_id="call-sarvam",
        stream_id="stream-sarvam",
        transport=CapturingTransport(),
    )

    assert isinstance(runner._tts, SarvamTextToSpeech)
    await runner.close()


@pytest.mark.anyio
async def test_factory_builds_luxtts_sidecar_tts() -> None:
    settings = Settings(
        environment="local",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="deepgram",
        deepgram_api_key="deepgram-secret",
        llm_provider="orchestrator",
        orchestrator_url="https://orchestrator.example.com/respond",
        tts_provider="luxtts",
        luxtts_tts_url="http://127.0.0.1:8811/tts",
        luxtts_api_key="lux-secret",
        luxtts_voice="maya",
        luxtts_model="YatharthS/LuxTTS",
        luxtts_num_steps=3,
        luxtts_t_shift=0.8,
        luxtts_speed=0.75,
        luxtts_min_first_chunk_chars=40,
        luxtts_min_soft_chunk_chars=48,
        luxtts_max_soft_chunk_chars=104,
        luxtts_timeout_seconds=7.5,
        luxtts_max_audio_bytes=123456,
    )

    runner = await build_media_session_runner(
        settings=settings,
        call_id="call-luxtts",
        stream_id="stream-luxtts",
        transport=CapturingTransport(),
    )

    assert isinstance(runner._tts, LuxTTSHttpTextToSpeech)
    assert runner._tts._url == "http://127.0.0.1:8811/tts"
    assert runner._tts._api_key == "lux-secret"
    assert runner._tts._voice == "maya"
    assert runner._tts._model == "YatharthS/LuxTTS"
    assert runner._tts._num_steps == 3
    assert runner._tts._t_shift == 0.8
    assert runner._tts._speed == 0.75
    assert runner._tts._timeout_seconds == 7.5
    assert runner._tts._max_audio_bytes == 123456
    assert runner._audio_cache_context is not None
    assert runner._audio_cache_context.tts_provider == "luxtts"
    assert runner._audio_cache_context.voice == "maya"
    assert runner._audio_cache_context.model == "YatharthS/LuxTTS"
    assert runner._audio_cache_context.pace == 0.75
    assert runner._reply_chunking_config.min_first_phrase_chars == 40
    assert runner._reply_chunking_config.min_soft_phrase_chars == 48
    assert runner._reply_chunking_config.max_soft_phrase_chars == 104
    assert runner._reply_chunking_config.max_early_phrase_chars == 104
    await runner.close()


def _cpu_local_settings(**overrides: object) -> Settings:
    # Model paths intentionally do NOT exist: the factory and provider constructors
    # must stay lazy (no filesystem access, no engine load) until media flows.
    base: dict[str, object] = dict(
        environment="local",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="sherpa",
        sherpa_stt_model_dir="models/nonexistent-zipformer",
        sherpa_stt_vad_model_path="models/nonexistent-silero.onnx",
        llm_provider="local",
        tts_provider="kokoro",
        kokoro_model_path="models/nonexistent-kokoro.onnx",
        kokoro_voices_path="models/nonexistent-voices.bin",
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_factory_builds_sherpa_stt_without_touching_model_files() -> None:
    settings = _cpu_local_settings(
        sherpa_stt_num_threads=3,
        sherpa_stt_semantic_silence_ms=220,
        sherpa_stt_endpoint_silence_ms=480,
        sherpa_stt_hard_silence_ms=1100,
        sherpa_stt_smart_turn_model_path="models/nonexistent-smart-turn.onnx",
        sherpa_stt_smart_turn_threshold=0.7,
    )

    runner = await build_media_session_runner(
        settings=settings,
        call_id="call-sherpa",
        stream_id="stream-sherpa",
        transport=CapturingTransport(),
    )

    assert isinstance(runner._stt, SherpaStreamingSpeechToText)
    stt = runner._stt
    assert stt._model_dir == "models/nonexistent-zipformer"
    assert stt._vad_model_path == "models/nonexistent-silero.onnx"
    assert stt._num_threads == 3
    assert stt._endpoint_config.semantic_check_silence_seconds == pytest.approx(0.22)
    assert stt._endpoint_config.endpoint_silence_seconds == pytest.approx(0.48)
    assert stt._endpoint_config.hard_silence_seconds == pytest.approx(1.1)
    assert isinstance(stt._semantic, SmartTurnSemanticEndpointer)
    assert stt._semantic.threshold == 0.7
    await runner.close()


@pytest.mark.anyio
async def test_factory_disables_semantic_endpointing_when_smart_turn_path_blank() -> None:
    settings = _cpu_local_settings(sherpa_stt_smart_turn_model_path="   ")

    runner = await build_media_session_runner(
        settings=settings,
        call_id="call-sherpa-no-semantic",
        stream_id="stream-sherpa-no-semantic",
        transport=CapturingTransport(),
    )

    assert isinstance(runner._stt, SherpaStreamingSpeechToText)
    assert runner._stt._semantic is None
    await runner.close()


@pytest.mark.anyio
async def test_factory_maps_sherpa_stt_overrides_onto_silence_gates() -> None:
    # endpointing_ms drives the semantic gate; utterance_end_ms drives the hard cap.
    runner = await build_media_session_runner(
        settings=_cpu_local_settings(),
        call_id="call-sherpa-override",
        stream_id="stream-sherpa-override",
        transport=CapturingTransport(),
        stt_override={"endpointing_ms": 250, "utterance_end_ms": 1500},
    )

    assert isinstance(runner._stt, SherpaStreamingSpeechToText)
    config = runner._stt._endpoint_config
    assert config.semantic_check_silence_seconds == pytest.approx(0.25)
    assert config.endpoint_silence_seconds == pytest.approx(0.4)
    assert config.hard_silence_seconds == pytest.approx(1.5)
    await runner.close()


@pytest.mark.anyio
async def test_factory_reclamps_inconsistent_sherpa_stt_overrides() -> None:
    # A huge endpointing_ms would invert the gate ordering; the factory re-clamps the
    # endpoint and hard gates upward so construction never raises.
    runner = await build_media_session_runner(
        settings=_cpu_local_settings(),
        call_id="call-sherpa-clamp",
        stream_id="stream-sherpa-clamp",
        transport=CapturingTransport(),
        stt_override={"endpointing_ms": 5000},
    )

    assert isinstance(runner._stt, SherpaStreamingSpeechToText)
    config = runner._stt._endpoint_config
    assert config.semantic_check_silence_seconds == pytest.approx(5.0)
    assert config.endpoint_silence_seconds == pytest.approx(5.0)
    assert config.hard_silence_seconds == pytest.approx(5.0)
    assert (
        config.hard_silence_seconds
        >= config.endpoint_silence_seconds
        >= config.semantic_check_silence_seconds
    )
    await runner.close()


@pytest.mark.anyio
async def test_factory_ignores_invalid_sherpa_stt_overrides() -> None:
    runner = await build_media_session_runner(
        settings=_cpu_local_settings(),
        call_id="call-sherpa-bad-override",
        stream_id="stream-sherpa-bad-override",
        transport=CapturingTransport(),
        stt_override={"endpointing_ms": 0, "utterance_end_ms": "soon"},
    )

    assert isinstance(runner._stt, SherpaStreamingSpeechToText)
    config = runner._stt._endpoint_config
    assert config.semantic_check_silence_seconds == pytest.approx(0.2)  # env default 200ms
    assert config.endpoint_silence_seconds == pytest.approx(0.4)
    assert config.hard_silence_seconds == pytest.approx(0.9)
    await runner.close()


@pytest.mark.anyio
async def test_factory_builds_kokoro_tts_with_chunking_and_cache_context() -> None:
    settings = _cpu_local_settings(
        kokoro_voice="af_sky",
        kokoro_speed=1.2,
        kokoro_lang="en-gb",
        kokoro_min_first_chunk_chars=30,
        kokoro_min_soft_chunk_chars=40,
        kokoro_max_soft_chunk_chars=110,
    )

    runner = await build_media_session_runner(
        settings=settings,
        call_id="call-kokoro",
        stream_id="stream-kokoro",
        transport=CapturingTransport(),
    )

    assert isinstance(runner._tts, KokoroTextToSpeech)
    tts = runner._tts
    assert tts._model_path == "models/nonexistent-kokoro.onnx"
    assert tts._voices_path == "models/nonexistent-voices.bin"
    assert tts._voice == "af_sky"
    assert tts._speed == 1.2
    assert tts._lang == "en-gb"
    assert runner._reply_chunking_config.min_first_phrase_chars == 30
    assert runner._reply_chunking_config.min_soft_phrase_chars == 40
    assert runner._reply_chunking_config.max_soft_phrase_chars == 110
    assert runner._reply_chunking_config.max_early_phrase_chars == 110
    assert runner._audio_cache_context is not None
    assert runner._audio_cache_context.tts_provider == "kokoro"
    assert runner._audio_cache_context.voice == "af_sky"
    assert runner._audio_cache_context.model == "models/nonexistent-kokoro.onnx"
    assert runner._audio_cache_context.language == "en-gb"
    assert runner._audio_cache_context.pace == 1.2
    await runner.close()


@pytest.mark.anyio
async def test_factory_honors_per_call_kokoro_voice_override() -> None:
    runner = await build_media_session_runner(
        settings=_cpu_local_settings(),
        call_id="call-kokoro-voice",
        stream_id="stream-kokoro-voice",
        transport=CapturingTransport(),
        tts_override={"voice": " am_adam "},
    )

    assert isinstance(runner._tts, KokoroTextToSpeech)
    assert runner._tts._voice == "am_adam"
    assert runner._audio_cache_context is not None
    assert runner._audio_cache_context.voice == "am_adam"
    await runner.close()


@pytest.mark.anyio
async def test_factory_builds_all_local_runner() -> None:
    settings = Settings(
        environment="local",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="local",
        tts_provider="local",
    )

    runner = await build_media_session_runner(
        settings=settings,
        call_id="call-local",
        stream_id="stream-local",
        transport=CapturingTransport(),
    )

    assert isinstance(runner._stt, LocalSpeechToText)
    assert isinstance(runner._llm, LocalLLMClient)
    assert isinstance(runner._tts, LocalTextToSpeech)
    await runner.close()
