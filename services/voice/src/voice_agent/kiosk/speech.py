"""The speech engines for one kiosk voice session, chosen by the session's language (M2-02).

`config/providers.yaml` `speech.stt|tts.{provider, by_language}` picks the engine per language:
STT `mlx-whisper` (English and Arabic, never auto-detecting), `deepgram` (optional; needs
DEEPGRAM_API_KEY) or `cohere-transcribe-arabic` (Arabic; gated, owner HF token needed); TTS
`kokoro` (English) or `mms-tts-ara` (Arabic). VAD: Silero, for barge-in. The agent's LLM client and
Gemini Live are never used on the kiosk path.
"""

from __future__ import annotations

from typing import Protocol

from voice_agent.config.careflow import LANGUAGE, SpeechConfig, load_speech_config
from voice_agent.config.settings import Settings
from voice_agent.providers._silero_vad import SileroVoiceActivityDetector
from voice_agent.providers.base import SpeechToText, TextToSpeech
from voice_agent.providers.cohere_transcribe_stt import cohere_stt, ensure_accessible
from voice_agent.providers.deepgram import DeepgramStreamingSpeechToText
from voice_agent.providers.kokoro_tts import KokoroTextToSpeech
from voice_agent.providers.mlx_whisper_stt import MlxWhisperSTT, VoiceActivity
from voice_agent.providers.mms_tts import MmsTextToSpeech


class KioskSpeech(Protocol):
    """Builds fresh engines for each connection (the heavy models are shared process-wide)."""

    def stt(self, language: str = LANGUAGE) -> SpeechToText: ...

    def tts(self, language: str = LANGUAGE) -> TextToSpeech: ...

    def vad(self) -> VoiceActivity: ...


class LocalKioskSpeech:
    def __init__(self, settings: Settings, speech: SpeechConfig | None = None) -> None:
        if settings.stt_provider not in ("mlx-whisper", "deepgram"):
            raise ValueError(f"stt_provider={settings.stt_provider} is not used on the kiosk path")
        if settings.tts_provider != "kokoro":
            raise ValueError(f"tts_provider={settings.tts_provider} is not used on the kiosk path")
        self._settings = settings
        self._speech = speech or load_speech_config()
        self._speech.validate_engines()  # an unknown engine fails here, at startup
        if self._speech.stt_provider("ar") == "cohere-transcribe-arabic":
            ensure_accessible()  # gated: refuse to start rather than fall back silently

    def stt(self, language: str = LANGUAGE) -> SpeechToText:
        settings = self._settings
        engine = self._speech.stt_provider(language)
        if language != LANGUAGE and engine == "mlx-whisper":
            return self._mlx_whisper(language)
        if engine == "cohere-transcribe-arabic":
            return cohere_stt(
                vad_model_path=settings.sherpa_stt_vad_model_path,
                smart_turn_model_path=settings.sherpa_stt_smart_turn_model_path,
                smart_turn_threshold=settings.sherpa_stt_smart_turn_threshold,
                semantic_check_silence_ms=settings.sherpa_stt_semantic_silence_ms,
                endpoint_silence_ms=settings.sherpa_stt_endpoint_silence_ms,
                hard_silence_ms=settings.sherpa_stt_hard_silence_ms,
            )
        if settings.stt_provider == "deepgram":
            if settings.deepgram_api_key is None:
                raise ValueError("stt_provider=deepgram requires DEEPGRAM_API_KEY.")
            return DeepgramStreamingSpeechToText(
                api_key=settings.deepgram_api_key.get_secret_value(),
                language=settings.mlx_whisper_language,
                endpointing_ms=settings.deepgram_endpointing_ms,
                utterance_end_ms=settings.deepgram_utterance_end_ms,
            )
        return self._mlx_whisper(settings.mlx_whisper_language)

    def _mlx_whisper(self, language: str) -> MlxWhisperSTT:
        settings = self._settings
        return MlxWhisperSTT(
            vad_model_path=settings.sherpa_stt_vad_model_path,
            model=settings.mlx_whisper_model,
            language=language,  # the session's language: never auto-detected
            smart_turn_model_path=settings.sherpa_stt_smart_turn_model_path,
            smart_turn_threshold=settings.sherpa_stt_smart_turn_threshold,
            semantic_check_silence_ms=settings.sherpa_stt_semantic_silence_ms,
            endpoint_silence_ms=settings.sherpa_stt_endpoint_silence_ms,
            hard_silence_ms=settings.sherpa_stt_hard_silence_ms,
        )

    def tts(self, language: str = LANGUAGE) -> TextToSpeech:
        settings = self._settings
        if self._speech.tts_provider(language) == "mms-tts-ara":
            return MmsTextToSpeech(model_path=settings.mms_tts_model_path)
        return KokoroTextToSpeech(
            model_path=settings.kokoro_model_path,
            voices_path=settings.kokoro_voices_path,
            voice=settings.kokoro_voice,
            speed=settings.kokoro_speed,
            lang=settings.kokoro_lang,
            first_unit_max_chars=settings.kokoro_first_unit_max_chars,
        )

    def vad(self) -> VoiceActivity:
        return SileroVoiceActivityDetector(model_path=self._settings.sherpa_stt_vad_model_path)
