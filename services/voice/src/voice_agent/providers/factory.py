from __future__ import annotations

import math
from collections.abc import Callable
from typing import Literal, overload

from voice_agent.config.settings import Settings
from voice_agent.media.limits import MAX_PRE_RESPONSE_ACK_CHARS
from voice_agent.media.session import (
    HandoffTarget,
    MediaSessionRunner,
    ReplyChunkingConfig,
    VoiceActionRequest,
    VoiceTurnLatencySnapshot,
)
from voice_agent.providers.base import (
    LLMClient,
    PlivoMediaTransport,
    SpeechToText,
    TextToSpeech,
)
from voice_agent.providers.deepgram import DeepgramStreamingSpeechToText
from voice_agent.providers.elevenlabs import ElevenLabsTextToSpeech
from voice_agent.providers.google_live import GoogleLiveMediaSessionRunner, GoogleLiveRealtimeClient
from voice_agent.providers.kokoro_tts import KokoroTextToSpeech
from voice_agent.providers.local import LocalLLMClient, LocalSpeechToText, LocalTextToSpeech
from voice_agent.providers.luxtts import LuxTTSHttpTextToSpeech
from voice_agent.providers.orchestrator import OrchestratorLLMClient
from voice_agent.providers.sarvam import SarvamStreamingTextToSpeech, SarvamTextToSpeech
from voice_agent.providers.sherpa_stt import SherpaStreamingSpeechToText
from voice_agent.telephony.plivo import PlivoAudioFormat
from voice_agent.workflow.audio_cache import (
    AudioCacheContext,
    AudioCachePurpose,
    InMemoryAudioCache,
)

# Effectively disables barge-in (caller cannot interrupt) when a project turns it off.
_BARGE_IN_DISABLED_FRAMES = 1_000_000_000
_AUDIO_CACHE = InMemoryAudioCache(max_entries=256, max_bytes=8 * 1024 * 1024)


def _as_float(value: object, default: float) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            return default
        return float(value)
    return default


def _as_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        # int() raises on inf/nan; a per-call override must fail soft, not crash
        # media-session construction.
        if isinstance(value, float) and not math.isfinite(value):
            return default
        return int(value)
    return default


def _as_int_at_least(value: object, default: int, minimum: int) -> int:
    parsed = _as_int(value, default)
    if parsed < minimum:
        return default
    return parsed


def _as_bool(value: object, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _as_optional_float(value: object) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _override_str(value: object) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def _override_optional_str(value: object, default: str | None) -> str | None:
    if value is False:
        return None
    if isinstance(value, str):
        return value.strip() or None
    return default


def _override_pre_response_ack_text(value: object, default: str | None) -> str | None:
    if default is None:
        return None
    if value is False:
        return None
    if isinstance(value, str):
        clean_value = value.strip()
        if not clean_value:
            return None
        if len(clean_value) > MAX_PRE_RESPONSE_ACK_CHARS:
            return default
        return clean_value
    return default


def _system_prompt_with_handoff_roster(
    base_prompt: str | None,
    behavior: dict[str, object],
) -> str | None:
    handoffs = behavior.get("handoffs")
    if not isinstance(handoffs, list) or not handoffs:
        return base_prompt
    lines: list[str] = []
    for raw_handoff in handoffs[:10]:
        if not isinstance(raw_handoff, dict):
            continue
        name = _override_str(raw_handoff.get("target_agent_name")) or "Unnamed target"
        condition = _override_str(raw_handoff.get("condition")) or "No condition specified"
        instruction = _override_str(raw_handoff.get("instruction"))
        target = raw_handoff.get("target")
        model = None
        provider = None
        target_prompt = None
        if isinstance(target, dict):
            target_prompt = _override_str(target.get("system_prompt"))
            llm = target.get("llm")
            if isinstance(llm, dict):
                provider = _override_str(llm.get("provider"))
                model = _override_str(llm.get("model"))
        detail = f"- {name}: use when {condition}."
        if instruction:
            detail += f" Instruction: {instruction}"
        if provider or model:
            detail += f" Target model: {'/'.join(part for part in (provider, model) if part)}."
        if target_prompt:
            detail += f" Target prompt summary: {target_prompt[:500]}"
        lines.append(detail)
    if not lines:
        return base_prompt
    roster = (
        "Available handoff targets:\n"
        + "\n".join(lines)
        + "\nWhen a caller need matches a target, acknowledge the handoff, preserve the "
        "caller context, and answer using the target agent's role and constraints."
    )
    clean_base = (base_prompt or "").strip()
    return f"{clean_base}\n\n{roster}" if clean_base else roster


def _handoff_targets_from_behavior(behavior: dict[str, object]) -> tuple[HandoffTarget, ...]:
    handoffs = behavior.get("handoffs")
    if not isinstance(handoffs, list):
        return ()
    targets: list[HandoffTarget] = []
    for raw_handoff in handoffs[:10]:
        if not isinstance(raw_handoff, dict):
            continue
        target_id = _override_str(raw_handoff.get("target_agent_config_id"))
        condition = _override_str(raw_handoff.get("condition"))
        if target_id is None or condition is None:
            continue
        target = raw_handoff.get("target")
        targets.append(
            HandoffTarget(
                target_id=target_id,
                name=_override_str(raw_handoff.get("target_agent_name")) or "Handoff target",
                condition=condition,
                instruction=_override_str(raw_handoff.get("instruction")),
                target=target if isinstance(target, dict) else {},
            )
        )
    return tuple(targets)


def _handoff_system_prompt(
    settings: Settings,
    target: HandoffTarget,
    conversation_context: str,
) -> str:
    target_prompt = _override_str(target.target.get("system_prompt"))
    base_prompt = target_prompt or settings.orchestrator_system_prompt or ""
    parts = [
        base_prompt.strip(),
        (
            f"You are now handling this live call as {target.name} after a handoff. "
            "Continue naturally; do not restart the greeting."
        ),
        f"Handoff condition: {target.condition}",
    ]
    if target.instruction:
        parts.append(f"Handoff instruction: {target.instruction}")
    if conversation_context.strip():
        parts.append(f"Conversation so far:\n{conversation_context.strip()}")
    return "\n\n".join(part for part in parts if part)


def _build_handoff_llm_factory(
    settings: Settings,
) -> Callable[[HandoffTarget, str], LLMClient]:
    def build(target: HandoffTarget, conversation_context: str) -> LLMClient:
        llm = target.target.get("llm")
        llm_config = llm if isinstance(llm, dict) else {}
        provider = _override_str(llm_config.get("provider"))
        if provider == "google_live":
            raise RuntimeError(
                "google_live targets cannot be activated inside the STT/TTS pipeline."
            )
        return _build_llm(
            settings,
            provider_override=provider,
            model_override=_override_str(llm_config.get("model")),
            system_prompt_override=_handoff_system_prompt(
                settings,
                target,
                conversation_context,
            ),
            temperature_override=_as_optional_float(llm_config.get("temperature")),
        )

    return build


def _build_google_live_handoff_client_factory(
    settings: Settings,
) -> Callable[[HandoffTarget, str], tuple[GoogleLiveRealtimeClient, str | None]]:
    google_live_api_key = settings.google_live_api_key
    if google_live_api_key is None:
        raise RuntimeError("google_live provider requires google_live_api_key.")

    def build(
        target: HandoffTarget,
        conversation_context: str,
    ) -> tuple[GoogleLiveRealtimeClient, str | None]:
        llm = target.target.get("llm")
        llm_config = llm if isinstance(llm, dict) else {}
        provider = _override_str(llm_config.get("provider"))
        if provider not in (None, "google_live"):
            raise RuntimeError(
                "Only google_live targets can be activated inside a Google Live session."
            )
        model = _override_str(llm_config.get("model")) or settings.google_live_model
        return (
            GoogleLiveRealtimeClient(
                api_key=google_live_api_key.get_secret_value(),
                model=model,
                voice=settings.google_live_voice,
                temperature=_as_optional_float(llm_config.get("temperature")),
                input_sample_rate_hz=settings.google_live_input_sample_rate_hz,
                output_sample_rate_hz=settings.google_live_output_sample_rate_hz,
                connect_timeout_seconds=settings.google_live_connect_timeout_seconds,
            ),
            _handoff_system_prompt(settings, target, conversation_context),
        )

    return build


@overload
async def build_media_session_runner(
    *,
    settings: Settings,
    call_id: str,
    stream_id: str,
    transport: PlivoMediaTransport,
    llm_provider_override: Literal["google_live"],
    llm_model_override: str | None = None,
    llm_system_prompt_override: str | None = None,
    greeting_override: str | None = None,
    temperature_override: float | None = None,
    behavior_override: dict[str, object] | None = None,
    stt_override: dict[str, object] | None = None,
    tts_override: dict[str, object] | None = None,
    latency_observer: Callable[[VoiceTurnLatencySnapshot], object] | None = None,
    transcript_observer: Callable[[str], object] | None = None,
    voice_action_observer: Callable[[VoiceActionRequest], object] | None = None,
    hangup_observer: Callable[[str], object] | None = None,
) -> GoogleLiveMediaSessionRunner: ...


@overload
async def build_media_session_runner(
    *,
    settings: Settings,
    call_id: str,
    stream_id: str,
    transport: PlivoMediaTransport,
    llm_provider_override: (
        Literal["openai", "anthropic", "google", "orchestrator", "local"] | None
    ) = None,
    llm_model_override: str | None = None,
    llm_system_prompt_override: str | None = None,
    greeting_override: str | None = None,
    temperature_override: float | None = None,
    behavior_override: dict[str, object] | None = None,
    stt_override: dict[str, object] | None = None,
    tts_override: dict[str, object] | None = None,
    latency_observer: Callable[[VoiceTurnLatencySnapshot], object] | None = None,
    transcript_observer: Callable[[str], object] | None = None,
    voice_action_observer: Callable[[VoiceActionRequest], object] | None = None,
    hangup_observer: Callable[[str], object] | None = None,
) -> MediaSessionRunner: ...


@overload
async def build_media_session_runner(
    *,
    settings: Settings,
    call_id: str,
    stream_id: str,
    transport: PlivoMediaTransport,
    llm_provider_override: str,
    llm_model_override: str | None = None,
    llm_system_prompt_override: str | None = None,
    greeting_override: str | None = None,
    temperature_override: float | None = None,
    behavior_override: dict[str, object] | None = None,
    stt_override: dict[str, object] | None = None,
    tts_override: dict[str, object] | None = None,
    latency_observer: Callable[[VoiceTurnLatencySnapshot], object] | None = None,
    transcript_observer: Callable[[str], object] | None = None,
    voice_action_observer: Callable[[VoiceActionRequest], object] | None = None,
    hangup_observer: Callable[[str], object] | None = None,
) -> MediaSessionRunner | GoogleLiveMediaSessionRunner: ...


async def build_media_session_runner(
    *,
    settings: Settings,
    call_id: str,
    stream_id: str,
    transport: PlivoMediaTransport,
    llm_provider_override: str | None = None,
    llm_model_override: str | None = None,
    llm_system_prompt_override: str | None = None,
    greeting_override: str | None = None,
    temperature_override: float | None = None,
    behavior_override: dict[str, object] | None = None,
    stt_override: dict[str, object] | None = None,
    tts_override: dict[str, object] | None = None,
    latency_observer: Callable[[VoiceTurnLatencySnapshot], object] | None = None,
    transcript_observer: Callable[[str], object] | None = None,
    voice_action_observer: Callable[[VoiceActionRequest], object] | None = None,
    hangup_observer: Callable[[str], object] | None = None,
) -> MediaSessionRunner | GoogleLiveMediaSessionRunner:
    # Production safety (no local providers in production) is enforced in
    # Settings.validate_runtime_safety. Per-project behaviour knobs (from the
    # agent-config) override the env defaults for this call; bad values fall back.
    behavior = behavior_override or {}
    system_prompt = _system_prompt_with_handoff_roster(
        llm_system_prompt_override or settings.orchestrator_system_prompt,
        behavior,
    )
    handoff_targets = _handoff_targets_from_behavior(behavior)
    barge_in_enabled = behavior.get("barge_in", True) is not False
    if _is_google_live_provider(settings, llm_provider_override):
        return _build_google_live_runner(
            settings=settings,
            call_id=call_id,
            stream_id=stream_id,
            transport=transport,
            model_override=llm_model_override,
            system_prompt_override=system_prompt,
            greeting_override=greeting_override,
            temperature_override=temperature_override,
            behavior_override=behavior,
            latency_observer=latency_observer,
            transcript_observer=transcript_observer,
        )
    return MediaSessionRunner(
        call_id=call_id,
        stream_id=stream_id,
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=_build_stt(settings, stt_override),
        llm=_build_llm(
            settings,
            provider_override=llm_provider_override,
            model_override=llm_model_override,
            system_prompt_override=system_prompt,
            temperature_override=temperature_override,
        ),
        tts=_build_tts(settings, tts_override),
        audio_cache=_AUDIO_CACHE,
        audio_cache_context=_build_audio_cache_context(settings, tts_override),
        greeting=greeting_override,
        closing_message=_override_str(behavior.get("closing_message")),
        auto_hangup_after_closing=_as_bool(
            behavior.get("auto_hangup_after_closing"),
            True,
        ),
        hangup_observer=hangup_observer,
        latency_observer=latency_observer,
        transcript_observer=transcript_observer,
        voice_action_observer=voice_action_observer,
        handoff_targets=handoff_targets,
        handoff_llm_factory=(_build_handoff_llm_factory(settings) if handoff_targets else None),
        transport=transport,
        turn_timeout_seconds=_as_float(
            behavior.get("turn_timeout_seconds"), settings.turn_timeout_seconds
        ),
        fallback_timeout_seconds=settings.fallback_timeout_seconds,
        audio_ingest_timeout_seconds=settings.audio_ingest_timeout_seconds,
        plivo_send_timeout_seconds=settings.plivo_send_timeout_seconds,
        provider_close_timeout_seconds=settings.provider_close_timeout_seconds,
        max_transcript_chars=settings.max_transcript_chars,
        max_reply_chars=_as_int(behavior.get("max_reply_chars"), settings.max_reply_chars),
        max_turns_per_call=settings.max_turns_per_call,
        max_play_audio_bytes=settings.max_play_audio_bytes,
        pre_response_ack_text=_override_pre_response_ack_text(
            behavior.get("pre_response_ack_text"),
            settings.pre_response_ack_text,
        ),
        pre_response_ack_delay_seconds=(
            _as_int_at_least(
                behavior.get("pre_response_ack_delay_ms"),
                settings.pre_response_ack_delay_ms,
                0,
            )
            / 1000.0
        ),
        local_end_of_turn_silence_seconds=(
            _as_int_at_least(
                behavior.get("local_end_of_turn_silence_ms"),
                settings.local_end_of_turn_silence_ms,
                0,
            )
            / 1000.0
        ),
        fast_final_end_of_turn_silence_seconds=(
            _as_int_at_least(
                behavior.get("fast_final_end_of_turn_silence_ms"),
                settings.fast_final_end_of_turn_silence_ms,
                0,
            )
            / 1000.0
        ),
        interim_transcript_stability_seconds=(
            _as_int_at_least(
                behavior.get("interim_transcript_stability_ms"),
                settings.interim_transcript_stability_ms,
                0,
            )
            / 1000.0
        ),
        interim_local_silence_commit_enabled=_as_bool(
            behavior.get("interim_local_silence_commit_enabled"),
            settings.interim_local_silence_commit_enabled,
        ),
        # The sherpa engine's chunked look-ahead makes interim transcripts trail the
        # flushed final by the last word or two, so speculative prewarm text never
        # matches the committed turn (measured 100% discards) — default it off there;
        # an explicit per-call behavior override can still force it on.
        speculative_response_prewarm=_as_bool(
            behavior.get("speculative_response_prewarm"),
            settings.speculative_response_prewarm and settings.stt_provider != "sherpa",
        ),
        speculative_response_stability_seconds=(
            _as_int_at_least(
                behavior.get("speculative_response_stability_ms"),
                settings.speculative_response_stability_ms,
                0,
            )
            / 1000.0
        ),
        speculative_response_min_chars=_as_int_at_least(
            behavior.get("speculative_response_min_chars"),
            settings.speculative_response_min_chars,
            0,
        ),
        barge_in_min_frames=(
            settings.barge_in_min_frames if barge_in_enabled else _BARGE_IN_DISABLED_FRAMES
        ),
        barge_in_energy_threshold=_as_float(
            behavior.get("barge_in_energy_threshold"), settings.barge_in_energy_threshold
        ),
        reply_chunking_config=_reply_chunking_config(settings),
        playback_checkpoints=settings.playback_checkpoints_enabled,
    )


def _reply_chunking_config(settings: Settings) -> ReplyChunkingConfig:
    if settings.tts_provider == "luxtts":
        return ReplyChunkingConfig(
            min_first_phrase_chars=settings.luxtts_min_first_chunk_chars,
            min_soft_phrase_chars=settings.luxtts_min_soft_chunk_chars,
            max_soft_phrase_chars=settings.luxtts_max_soft_chunk_chars,
            max_early_phrase_chars=max(
                settings.luxtts_max_soft_chunk_chars,
                settings.luxtts_min_first_chunk_chars,
            ),
        )
    if settings.tts_provider == "kokoro":
        # Local synthesis is fast enough that a small first phrase minimizes
        # time-to-first-audio without starving the engine of prosody context.
        return ReplyChunkingConfig(
            min_first_phrase_chars=settings.kokoro_min_first_chunk_chars,
            min_soft_phrase_chars=settings.kokoro_min_soft_chunk_chars,
            max_soft_phrase_chars=settings.kokoro_max_soft_chunk_chars,
            max_early_phrase_chars=max(
                settings.kokoro_max_soft_chunk_chars,
                settings.kokoro_min_first_chunk_chars,
            ),
        )
    return ReplyChunkingConfig()


async def generate_preview_reply(
    *,
    settings: Settings,
    call_id: str,
    user_text: str,
    llm_provider_override: str | None = None,
    llm_model_override: str | None = None,
    llm_system_prompt_override: str | None = None,
    temperature_override: float | None = None,
) -> str:
    llm = _build_llm(
        settings,
        provider_override=llm_provider_override,
        model_override=llm_model_override,
        system_prompt_override=llm_system_prompt_override,
        temperature_override=temperature_override,
    )
    parts: list[str] = []
    try:
        async for chunk in llm.generate_reply(call_id=call_id, user_text=user_text):
            parts.append(chunk)
    finally:
        await llm.close()
    return "".join(parts).strip()


async def generate_tts_preview_audio(
    *,
    settings: Settings,
    text: str,
    tts_override: dict[str, object] | None = None,
    audio_format: PlivoAudioFormat = PlivoAudioFormat.L16_16KHZ,
) -> bytes:
    clean_text = text.strip()
    if not clean_text:
        raise ValueError("Preview text must not be blank.")
    tts = _build_tts(settings, tts_override)
    chunks: list[bytes] = []
    try:
        async for chunk in tts.synthesize(text=clean_text, audio_format=audio_format):
            if chunk:
                chunks.append(chunk)
    finally:
        await tts.close()
    return b"".join(chunks)


async def prewarm_cached_tts_audio(
    *,
    settings: Settings,
    text: str,
    purpose: AudioCachePurpose,
    tts_override: dict[str, object] | None = None,
    audio_format: PlivoAudioFormat = PlivoAudioFormat.L16_16KHZ,
) -> bool:
    clean_text = text.strip()
    if not clean_text:
        return False
    context = _build_audio_cache_context(settings, tts_override)
    key = context.key(
        purpose=purpose,
        text=clean_text,
        audio_format=audio_format.content_type,
        sample_rate=audio_format.sample_rate,
    )
    if _AUDIO_CACHE.get(key) is not None:
        return True
    tts = _build_tts(settings, tts_override)
    chunks: list[bytes] = []
    try:
        async for chunk in tts.synthesize(text=clean_text, audio_format=audio_format):
            if chunk:
                chunks.append(chunk)
    finally:
        await tts.close()
    audio = b"".join(chunks)
    if not audio:
        return False
    _AUDIO_CACHE.set(key, audio)
    return True


def _build_stt(settings: Settings, override: dict[str, object] | None = None) -> SpeechToText:
    # A project's agent-config can override the recognition language; the engine + key are
    # fixed by the deployment (the agent only ships the Deepgram engine).
    override = override or {}
    if settings.stt_provider == "deepgram":
        if not settings.deepgram_api_key:
            raise RuntimeError("stt_provider=deepgram requires deepgram_api_key.")
        return DeepgramStreamingSpeechToText(
            api_key=settings.deepgram_api_key.get_secret_value(),
            language=_override_str(override.get("language")) or "en-IN",
            endpointing_ms=_as_int_at_least(
                override.get("endpointing_ms"),
                settings.deepgram_endpointing_ms,
                1,
            ),
            utterance_end_ms=_as_int_at_least(
                override.get("utterance_end_ms"),
                settings.deepgram_utterance_end_ms,
                1000,
            ),
            connect_timeout_seconds=settings.deepgram_connect_timeout_seconds,
            ping_interval_seconds=settings.deepgram_ping_interval_seconds,
            ping_timeout_seconds=settings.deepgram_ping_timeout_seconds,
            transcript_queue_max_size=settings.deepgram_transcript_queue_max_size,
            transcript_message_max_bytes=settings.deepgram_transcript_message_max_bytes,
            audio_queue_max_size=settings.deepgram_audio_queue_max_size,
        )
    if settings.stt_provider == "sherpa":
        # Per-call agent-config can tighten/loosen endpointing (same override keys the
        # Deepgram engine honors: endpointing_ms -> the fast gate, utterance_end_ms ->
        # the hard cap); the gates are re-clamped so an aggressive override can never
        # produce an inconsistent config. Model paths are fixed by the deployment, and
        # the engine is English-only, so a per-call `language` override is ignored
        # (fail-soft, like other invalid overrides).
        semantic_silence_ms = _as_int_at_least(
            override.get("endpointing_ms"),
            settings.sherpa_stt_semantic_silence_ms,
            1,
        )
        endpoint_silence_ms = max(settings.sherpa_stt_endpoint_silence_ms, semantic_silence_ms)
        hard_silence_ms = max(
            _as_int_at_least(
                override.get("utterance_end_ms"),
                settings.sherpa_stt_hard_silence_ms,
                1,
            ),
            endpoint_silence_ms,
        )
        return SherpaStreamingSpeechToText(
            model_dir=settings.sherpa_stt_model_dir,
            vad_model_path=settings.sherpa_stt_vad_model_path,
            num_threads=settings.sherpa_stt_num_threads,
            semantic_check_silence_ms=semantic_silence_ms,
            endpoint_silence_ms=endpoint_silence_ms,
            hard_silence_ms=hard_silence_ms,
            smart_turn_model_path=settings.sherpa_stt_smart_turn_model_path,
            smart_turn_threshold=settings.sherpa_stt_smart_turn_threshold,
        )
    return LocalSpeechToText()


def _build_llm(
    settings: Settings,
    *,
    provider_override: str | None = None,
    model_override: str | None = None,
    system_prompt_override: str | None = None,
    temperature_override: float | None = None,
) -> LLMClient:
    # Per-call overrides (a project's agent-config, carried from the outbound
    # request) win over the process-wide env defaults; otherwise fall back to env.
    if settings.llm_provider == "orchestrator":
        if not settings.orchestrator_url:
            raise RuntimeError("llm_provider=orchestrator requires orchestrator_url.")
        return OrchestratorLLMClient(
            url=settings.orchestrator_url,
            api_key=(
                settings.orchestrator_api_key.get_secret_value()
                if settings.orchestrator_api_key
                else None
            ),
            timeout_seconds=settings.orchestrator_timeout_seconds,
            first_chunk_timeout_seconds=settings.orchestrator_first_chunk_timeout_seconds,
            require_streaming_response=settings.orchestrator_require_streaming_response,
            max_response_bytes=settings.orchestrator_max_response_bytes,
            system_prompt=system_prompt_override or settings.orchestrator_system_prompt,
            provider=provider_override or settings.orchestrator_provider,
            model=model_override or settings.orchestrator_model,
            temperature=temperature_override,
            retry_max_attempts=settings.provider_retry_max_attempts,
            retry_backoff_seconds=settings.provider_retry_backoff_seconds,
            retry_max_backoff_seconds=settings.provider_retry_max_backoff_seconds,
        )
    return LocalLLMClient()


def _build_audio_cache_context(
    settings: Settings,
    override: dict[str, object] | None = None,
) -> AudioCacheContext:
    override = override or {}
    voice = _override_str(override.get("voice"))
    tts_model = _override_str(override.get("model"))
    if settings.tts_provider == "sarvam":
        return AudioCacheContext(
            tts_provider="sarvam",
            voice=voice or settings.sarvam_speaker,
            model=tts_model or settings.sarvam_model,
            language=settings.sarvam_target_language_code,
            pace=settings.sarvam_pace,
        )
    if settings.tts_provider == "elevenlabs":
        return AudioCacheContext(
            tts_provider="elevenlabs",
            voice=voice or settings.elevenlabs_voice_id,
            model=tts_model or settings.elevenlabs_model_id,
        )
    if settings.tts_provider == "luxtts":
        return AudioCacheContext(
            tts_provider="luxtts",
            voice=voice or settings.luxtts_voice,
            model=tts_model or settings.luxtts_model,
            language="en-IN",
            pace=settings.luxtts_speed,
        )
    if settings.tts_provider == "kokoro":
        return AudioCacheContext(
            tts_provider="kokoro",
            voice=voice or settings.kokoro_voice,
            model=settings.kokoro_model_path,
            language=settings.kokoro_lang,
            pace=settings.kokoro_speed,
        )
    return AudioCacheContext(tts_provider="local")


def _build_tts(settings: Settings, override: dict[str, object] | None = None) -> TextToSpeech:
    # A project's agent-config can override the voice + TTS model; the engine + key are fixed
    # by the deployment.
    override = override or {}
    voice = _override_str(override.get("voice"))
    tts_model = _override_str(override.get("model"))
    if settings.tts_provider == "sarvam":
        if not settings.sarvam_api_key:
            raise RuntimeError("tts_provider=sarvam requires sarvam_api_key.")
        if settings.sarvam_streaming:
            return SarvamStreamingTextToSpeech(
                api_key=settings.sarvam_api_key.get_secret_value(),
                speaker=voice or settings.sarvam_speaker,
                model=tts_model or settings.sarvam_model,
                target_language_code=settings.sarvam_target_language_code,
                pace=settings.sarvam_pace,
                temperature=settings.sarvam_temperature,
                min_buffer_size=settings.sarvam_min_buffer_size,
                api_base_url=settings.sarvam_api_base_url,
                connect_timeout_seconds=settings.sarvam_timeout_seconds,
                read_timeout_seconds=settings.sarvam_read_timeout_seconds,
                max_audio_bytes=settings.sarvam_max_audio_bytes,
                retry_max_attempts=settings.provider_retry_max_attempts,
                retry_backoff_seconds=settings.provider_retry_backoff_seconds,
                retry_max_backoff_seconds=settings.provider_retry_max_backoff_seconds,
            )
        return SarvamTextToSpeech(
            api_key=settings.sarvam_api_key.get_secret_value(),
            speaker=voice or settings.sarvam_speaker,
            model=tts_model or settings.sarvam_model,
            target_language_code=settings.sarvam_target_language_code,
            pace=settings.sarvam_pace,
            temperature=settings.sarvam_temperature,
            enable_preprocessing=settings.sarvam_enable_preprocessing,
            api_base_url=settings.sarvam_api_base_url,
            timeout_seconds=settings.sarvam_timeout_seconds,
            read_timeout_seconds=settings.sarvam_read_timeout_seconds,
            max_audio_bytes=settings.sarvam_max_audio_bytes,
            retry_max_attempts=settings.provider_retry_max_attempts,
            retry_backoff_seconds=settings.provider_retry_backoff_seconds,
            retry_max_backoff_seconds=settings.provider_retry_max_backoff_seconds,
        )
    if settings.tts_provider == "elevenlabs":
        if not settings.elevenlabs_api_key or not settings.elevenlabs_voice_id:
            raise RuntimeError(
                "tts_provider=elevenlabs requires elevenlabs_api_key and elevenlabs_voice_id."
            )
        return ElevenLabsTextToSpeech(
            api_key=settings.elevenlabs_api_key.get_secret_value(),
            voice_id=voice or settings.elevenlabs_voice_id,
            model_id=tts_model or settings.elevenlabs_model_id,
            timeout_seconds=settings.elevenlabs_timeout_seconds,
            max_audio_bytes=settings.elevenlabs_max_audio_bytes,
            retry_max_attempts=settings.provider_retry_max_attempts,
            retry_backoff_seconds=settings.provider_retry_backoff_seconds,
            retry_max_backoff_seconds=settings.provider_retry_max_backoff_seconds,
        )
    if settings.tts_provider == "kokoro":
        return KokoroTextToSpeech(
            model_path=settings.kokoro_model_path,
            voices_path=settings.kokoro_voices_path,
            voice=voice or settings.kokoro_voice,
            speed=settings.kokoro_speed,
            lang=settings.kokoro_lang,
            first_unit_max_chars=settings.kokoro_first_unit_max_chars,
        )
    if settings.tts_provider == "luxtts":
        if not settings.luxtts_tts_url:
            raise RuntimeError("tts_provider=luxtts requires luxtts_tts_url.")
        return LuxTTSHttpTextToSpeech(
            url=settings.luxtts_tts_url,
            api_key=(
                settings.luxtts_api_key.get_secret_value() if settings.luxtts_api_key else None
            ),
            voice=voice or settings.luxtts_voice,
            model=tts_model or settings.luxtts_model,
            num_steps=settings.luxtts_num_steps,
            t_shift=settings.luxtts_t_shift,
            speed=settings.luxtts_speed,
            timeout_seconds=settings.luxtts_timeout_seconds,
            max_audio_bytes=settings.luxtts_max_audio_bytes,
            retry_max_attempts=settings.provider_retry_max_attempts,
            retry_backoff_seconds=settings.provider_retry_backoff_seconds,
            retry_max_backoff_seconds=settings.provider_retry_max_backoff_seconds,
        )
    return LocalTextToSpeech()


def _is_google_live_provider(settings: Settings, provider_override: str | None) -> bool:
    provider = provider_override or settings.llm_provider
    return provider.strip().lower() == "google_live"


def _build_google_live_runner(
    *,
    settings: Settings,
    call_id: str,
    stream_id: str,
    transport: PlivoMediaTransport,
    model_override: str | None,
    system_prompt_override: str | None,
    greeting_override: str | None,
    temperature_override: float | None,
    behavior_override: dict[str, object],
    latency_observer: Callable[[VoiceTurnLatencySnapshot], object] | None,
    transcript_observer: Callable[[str], object] | None,
) -> GoogleLiveMediaSessionRunner:
    if settings.google_live_api_key is None:
        raise RuntimeError("google_live provider requires google_live_api_key.")
    model = model_override or settings.google_live_model
    handoff_targets = _handoff_targets_from_behavior(behavior_override)
    return GoogleLiveMediaSessionRunner(
        call_id=call_id,
        stream_id=stream_id,
        transport=transport,
        client=GoogleLiveRealtimeClient(
            api_key=settings.google_live_api_key.get_secret_value(),
            model=model,
            voice=settings.google_live_voice,
            temperature=temperature_override,
            input_sample_rate_hz=settings.google_live_input_sample_rate_hz,
            output_sample_rate_hz=settings.google_live_output_sample_rate_hz,
            connect_timeout_seconds=settings.google_live_connect_timeout_seconds,
        ),
        audio_format=PlivoAudioFormat.L16_16KHZ,
        system_instruction=system_prompt_override or settings.orchestrator_system_prompt,
        greeting=greeting_override,
        google_output_sample_rate_hz=settings.google_live_output_sample_rate_hz,
        audio_ingest_timeout_seconds=settings.audio_ingest_timeout_seconds,
        plivo_send_timeout_seconds=settings.plivo_send_timeout_seconds,
        provider_close_timeout_seconds=settings.provider_close_timeout_seconds,
        max_play_audio_bytes=_as_int(
            behavior_override.get("max_play_audio_bytes"),
            settings.max_play_audio_bytes,
        ),
        latency_observer=latency_observer,
        transcript_observer=transcript_observer,
        handoff_targets=handoff_targets,
        handoff_client_factory=(
            _build_google_live_handoff_client_factory(settings) if handoff_targets else None
        ),
    )
