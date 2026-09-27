"""The speech engines for one kiosk voice session, chosen by the settings.

STT: `mlx-whisper` (default, from config/providers.yaml) or `deepgram` (optional; needs
DEEPGRAM_API_KEY, never set by the unattended run). TTS: Kokoro. VAD: Silero, for barge-in.
The agent's LLM client and Gemini Live are never used on the kiosk path.
"""

from __future__ import annotations

from typing import Protocol

from voice_agent.config.settings import Settings
from voice_agent.providers._silero_vad import SileroVoiceActivityDetector
from voice_agent.providers.base import SpeechToText, TextToSpeech
from voice_agent.providers.deepgram import DeepgramStreamingSpeechToText
from voice_agent.providers.kokoro_tts import KokoroTextToSpeech
from voice_agent.providers.mlx_whisper_stt import MlxWhisperSTT, VoiceActivity


class KioskSpeech(Protocol):
    """Builds fresh engines for each connection (the heavy models are shared process-wide)."""

    def stt(self) -> SpeechToText: ...

    def tts(self) -> TextToSpeech: ...

    def vad(self) -> VoiceActivity: ...


class LocalKioskSpeech:
    def __init__(self, settings: Settings) -> None:
        if settings.stt_provider not in ("mlx-whisper", "deepgram"):
            raise ValueError(f"stt_provider={settings.stt_provider} is not used on the kiosk path")
        if settings.tts_provider != "kokoro":
            raise ValueError(f"tts_provider={settings.tts_provider} is not used on the kiosk path")
        self._settings = settings

    def stt(self) -> SpeechToText:
        settings = self._settings
        if settings.stt_provider == "deepgram":
            if settings.deepgram_api_key is None:
                raise ValueError("stt_provider=deepgram requires DEEPGRAM_API_KEY.")
            return DeepgramStreamingSpeechToText(
                api_key=settings.deepgram_api_key.get_secret_value(),
                language=settings.mlx_whisper_language,
                endpointing_ms=settings.deepgram_endpointing_ms,
                utterance_end_ms=settings.deepgram_utterance_end_ms,
            )
        return MlxWhisperSTT(
            vad_model_path=settings.sherpa_stt_vad_model_path,
            model=settings.mlx_whisper_model,
            language=settings.mlx_whisper_language,
            smart_turn_model_path=settings.sherpa_stt_smart_turn_model_path,
            smart_turn_threshold=settings.sherpa_stt_smart_turn_threshold,
            semantic_check_silence_ms=settings.sherpa_stt_semantic_silence_ms,
            endpoint_silence_ms=settings.sherpa_stt_endpoint_silence_ms,
            hard_silence_ms=settings.sherpa_stt_hard_silence_ms,
        )

    def tts(self) -> TextToSpeech:
        settings = self._settings
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
