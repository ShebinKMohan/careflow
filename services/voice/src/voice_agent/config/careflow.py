"""CareFlow's voice settings: speech providers from `config/providers.yaml`, local defaults.

`CareflowSettings` is the agent's `Settings` with one more source, below the environment and
`.env`: the `speech` section of `config/providers.yaml` picks the STT and TTS engines, and the
model files default to `services/voice/models/`. A `VOICE_AGENT_*` variable still wins, so a
single engine can be switched without editing the file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from voice_agent.config.settings import Settings

VOICE_ROOT: Final = Path(__file__).resolve().parents[3]
REPO: Final = VOICE_ROOT.parents[1]
PROVIDERS_CONFIG: Final = REPO / "config" / "providers.yaml"
MODELS_DIR: Final = VOICE_ROOT / "models"
LANGUAGE: Final = "en"
"""The default language; a kiosk session may pick Arabic (M2-01), and M2-02 speaks it."""
LANGUAGES: Final = ("en", "ar")

SpeechToTextName = Literal["mlx-whisper", "deepgram", "cohere-transcribe-arabic"]
TextToSpeechName = Literal["kokoro", "mms-tts-ara"]
STT_NAMES: Final = ("mlx-whisper", "deepgram", "cohere-transcribe-arabic")
TTS_NAMES: Final = ("kokoro", "mms-tts-ara")


class _Engine(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    by_language: dict[str, str] = Field(default_factory=dict[str, str])

    def for_language(self, language: str) -> str:
        return self.by_language.get(language, self.provider)


class SpeechConfig(BaseModel):
    """`speech:` in config/providers.yaml."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stt: _Engine
    tts: _Engine

    def stt_provider(self, language: str = LANGUAGE) -> SpeechToTextName:
        name = self.stt.for_language(language)
        if name not in STT_NAMES:
            raise ValueError(f"speech.stt provider {name!r} is not supported by the voice service")
        if name == "cohere-transcribe-arabic" and language != "ar":
            raise ValueError("speech.stt cohere-transcribe-arabic serves Arabic only")
        return name  # type: ignore[return-value]

    def tts_provider(self, language: str = LANGUAGE) -> TextToSpeechName:
        name = self.tts.for_language(language)
        if name not in TTS_NAMES:
            raise ValueError(f"speech.tts provider {name!r} is not supported by the voice service")
        if name == "mms-tts-ara" and language != "ar":
            raise ValueError("speech.tts mms-tts-ara speaks Arabic only")
        return name  # type: ignore[return-value]

    def validate_engines(self) -> None:
        """Every language's engines, at startup: an unknown provider never falls back silently."""
        for language in LANGUAGES:
            self.stt_provider(language)
            self.tts_provider(language)


DEFAULT_SPEECH: Final = SpeechConfig(
    stt=_Engine(provider="mlx-whisper"), tts=_Engine(provider="kokoro")
)


def load_speech_config(path: Path = PROVIDERS_CONFIG) -> SpeechConfig:
    """The `speech` section, or the defaults (mlx-whisper, Kokoro) when the file has none."""
    if not path.is_file():
        return DEFAULT_SPEECH
    data: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    speech = data.get("speech") if isinstance(data, dict) else None
    if speech is None:
        return DEFAULT_SPEECH
    return SpeechConfig.model_validate(speech)


def careflow_defaults(speech: SpeechConfig) -> dict[str, Any]:
    """Values CareFlow sets when neither the environment nor `.env` does."""
    return {
        "environment": "local",
        # One kiosk process keeps its state in memory; the voice service stores no transcripts.
        "session_backend": "memory",
        "stt_provider": speech.stt_provider(),
        # The kiosk path never calls the agent's LLM: the intake graph is the only "brain".
        "llm_provider": "local",
        "tts_provider": speech.tts_provider(),
        "sherpa_stt_vad_model_path": str(MODELS_DIR / "silero_vad.onnx"),
        "sherpa_stt_smart_turn_model_path": str(MODELS_DIR / "smart-turn-v3.2-cpu.onnx"),
        # A local directory (download_models.sh), so mlx-whisper never downloads on first use.
        "mlx_whisper_model": str(MODELS_DIR / "whisper-large-v3-turbo"),
        "mlx_whisper_language": LANGUAGE,
        "kokoro_model_path": str(MODELS_DIR / "kokoro-v1.0.onnx"),
        "kokoro_voices_path": str(MODELS_DIR / "voices-v1.0.bin"),
        "mms_tts_model_path": str(MODELS_DIR / "mms-tts-ara"),
        "log_format": "text",
    }


class _CareflowDefaultsSource(PydanticBaseSettingsSource):
    def __init__(self, settings_cls: type[BaseSettings], values: dict[str, Any]) -> None:
        super().__init__(settings_cls)
        self._values = values

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return self._values.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return dict(self._values)


class CareflowSettings(Settings):
    model_config = SettingsConfigDict(
        env_file=REPO / ".env",
        env_prefix="VOICE_AGENT_",
        extra="ignore",
        frozen=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        defaults = _CareflowDefaultsSource(settings_cls, careflow_defaults(load_speech_config()))
        return init_settings, env_settings, dotenv_settings, defaults, file_secret_settings
