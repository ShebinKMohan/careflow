"""Speech providers from config/providers.yaml, mapped onto the agent's settings."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from voice_agent.config.careflow import (
    DEFAULT_SPEECH,
    MODELS_DIR,
    PROVIDERS_CONFIG,
    CareflowSettings,
    load_speech_config,
)
from voice_agent.config.settings import get_settings


def test_the_repo_config_picks_mlx_whisper_and_kokoro_for_english() -> None:
    speech = load_speech_config(PROVIDERS_CONFIG)
    assert (speech.stt_provider("en"), speech.tts_provider("en")) == ("mlx-whisper", "kokoro")


def test_a_missing_file_or_section_falls_back_to_the_defaults(tmp_path: Path) -> None:
    assert load_speech_config(tmp_path / "absent.yaml") == DEFAULT_SPEECH
    empty = tmp_path / "providers.yaml"
    empty.write_text("tasks: {}\n", encoding="utf-8")
    assert load_speech_config(empty) == DEFAULT_SPEECH


def test_an_unsupported_engine_is_refused(tmp_path: Path) -> None:
    config = tmp_path / "providers.yaml"
    config.write_text("speech:\n  stt: {provider: whisper-cloud}\n  tts: {provider: kokoro}\n")
    with pytest.raises(ValueError, match="not supported"):
        load_speech_config(config).stt_provider()


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in (
        "VOICE_AGENT_STT_PROVIDER",
        "VOICE_AGENT_TTS_PROVIDER",
        "VOICE_AGENT_SESSION_BACKEND",
        "VOICE_PLIVO_ENABLED",
        "DEEPGRAM_API_KEY",
        "VOICE_AGENT_DEEPGRAM_API_KEY",
        "CAREFLOW_JWT_SECRET",
        "JWT_SECRET",
        "VOICE_PORT",  # scripts/stack.py exports a stack's own port
        "VOICE_HOST",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_careflow_settings_come_from_the_config_with_local_defaults(
    clean_env: pytest.MonkeyPatch,
) -> None:
    settings = CareflowSettings(_env_file=None)  # type: ignore[call-arg]
    assert (settings.stt_provider, settings.tts_provider) == ("mlx-whisper", "kokoro")
    assert (settings.llm_provider, settings.session_backend) == ("local", "memory")
    assert settings.plivo_enabled is False and settings.public_base_url is None
    assert settings.port == 8090 and settings.host == "127.0.0.1"
    assert Path(settings.mlx_whisper_model) == MODELS_DIR / "whisper-large-v3-turbo"
    assert Path(settings.kokoro_model_path).parent == MODELS_DIR
    assert settings.careflow_jwt_secret is None


def test_the_environment_still_wins(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("VOICE_AGENT_STT_PROVIDER", "deepgram")
    clean_env.setenv("DEEPGRAM_API_KEY", "dg-test-key")
    clean_env.setenv("CAREFLOW_JWT_SECRET", "jwt-test-secret")
    settings = CareflowSettings(_env_file=None)  # type: ignore[call-arg]
    assert settings.stt_provider == "deepgram"
    assert settings.deepgram_api_key is not None
    assert settings.deepgram_api_key.get_secret_value() == "dg-test-key"
    assert settings.careflow_jwt_secret is not None


def test_the_plivo_flag_needs_a_public_url(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("VOICE_PLIVO_ENABLED", "true")
    with pytest.raises(ValidationError, match="public_base_url is required"):
        CareflowSettings(_env_file=None)  # type: ignore[call-arg]


def test_get_settings_is_the_careflow_settings() -> None:
    get_settings.cache_clear()
    try:
        assert isinstance(get_settings(), CareflowSettings)
    finally:
        get_settings.cache_clear()
