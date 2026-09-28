"""The kiosk's speech factory (M2-02): `config/providers.yaml` `speech.stt|tts.by_language` picks
each language's engine, and an engine that cannot run fails at startup, never silently."""

from __future__ import annotations

from typing import Any

import pytest

from voice_agent.config.careflow import PROVIDERS_CONFIG, SpeechConfig, load_speech_config
from voice_agent.config.settings import Settings
from voice_agent.kiosk import speech as kiosk_speech
from voice_agent.kiosk.speech import LocalKioskSpeech
from voice_agent.providers.cohere_transcribe_stt import (
    GATED_REASON,
    MODEL_ID,
    CohereTranscribeSTT,
    CohereTranscribeUnavailableError,
)
from voice_agent.providers.kokoro_tts import KokoroTextToSpeech
from voice_agent.providers.mlx_whisper_stt import MlxWhisperSTT
from voice_agent.providers.mms_tts import MmsTextToSpeech


def _settings() -> Settings:
    return Settings(
        environment="test",
        session_backend="memory",
        stt_provider="mlx-whisper",
        llm_provider="local",
        tts_provider="kokoro",
        mlx_whisper_model="/models/whisper-large-v3-turbo",
    )


def _config(*, ar_stt: str = "mlx-whisper", ar_tts: str = "mms-tts-ara") -> SpeechConfig:
    return SpeechConfig.model_validate(
        {
            "stt": {"provider": "mlx-whisper", "by_language": {"en": "mlx-whisper", "ar": ar_stt}},
            "tts": {"provider": "kokoro", "by_language": {"en": "kokoro", "ar": ar_tts}},
        }
    )


@pytest.fixture
def cohere_unlocked(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The owner has unlocked the gated model: its snapshot is in the local cache."""
    checked: list[str] = []
    monkeypatch.setattr(kiosk_speech, "ensure_accessible", lambda: checked.append("checked"))
    return checked


def test_changing_only_by_language_ar_switches_mlx_whisper_and_cohere(
    cohere_unlocked: list[str],
) -> None:
    whisper = LocalKioskSpeech(_settings(), _config(ar_stt="mlx-whisper")).stt("ar")
    assert type(whisper) is MlxWhisperSTT
    assert (whisper._model, whisper._language) == ("/models/whisper-large-v3-turbo", "ar")
    assert cohere_unlocked == []  # mlx-whisper needs no Hugging Face check

    cohere = LocalKioskSpeech(_settings(), _config(ar_stt="cohere-transcribe-arabic")).stt("ar")
    assert type(cohere) is CohereTranscribeSTT
    assert (cohere._model, cohere._language) == (MODEL_ID, "ar")
    assert cohere_unlocked == ["checked"]  # checked once, at startup


def test_english_keeps_mlx_whisper_whichever_arabic_engine_is_chosen(
    cohere_unlocked: list[str],
) -> None:
    speech = LocalKioskSpeech(_settings(), _config(ar_stt="cohere-transcribe-arabic"))
    english = speech.stt("en")
    assert type(english) is MlxWhisperSTT and english._language == "en"


def test_the_session_language_reaches_whisper_never_detected() -> None:
    speech = LocalKioskSpeech(_settings(), _config())
    assert [speech.stt(lang)._language for lang in ("en", "ar")] == ["en", "ar"]  # type: ignore[attr-defined]


def test_a_gated_cohere_engine_refuses_to_start_instead_of_falling_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def gated() -> None:
        raise CohereTranscribeUnavailableError(f"{MODEL_ID} is not downloaded ({GATED_REASON})")

    monkeypatch.setattr(kiosk_speech, "ensure_accessible", gated)
    with pytest.raises(CohereTranscribeUnavailableError, match=GATED_REASON):
        LocalKioskSpeech(_settings(), _config(ar_stt="cohere-transcribe-arabic"))


def test_arabic_speaks_with_mms_and_english_with_kokoro() -> None:
    speech = LocalKioskSpeech(_settings(), _config())
    assert isinstance(speech.tts("ar"), MmsTextToSpeech)
    assert isinstance(speech.tts("en"), KokoroTextToSpeech)


@pytest.mark.parametrize(
    ("stt", "tts", "error"),
    [
        ({"provider": "whisper-cloud"}, {"provider": "kokoro"}, "not supported"),
        (
            {"provider": "mlx-whisper"},
            {"provider": "kokoro", "by_language": {"ar": "silma"}},
            "not supported",
        ),
        (
            {"provider": "mlx-whisper", "by_language": {"en": "cohere-transcribe-arabic"}},
            {"provider": "kokoro"},
            "Arabic only",
        ),
        (
            {"provider": "mlx-whisper"},
            {"provider": "kokoro", "by_language": {"en": "mms-tts-ara"}},
            "Arabic only",
        ),
    ],
    ids=["unknown-stt", "unknown-tts", "cohere-for-english", "mms-for-english"],
)
def test_an_engine_that_cannot_serve_a_language_fails_at_startup(
    stt: dict[str, Any], tts: dict[str, Any], error: str
) -> None:
    config = SpeechConfig.model_validate({"stt": stt, "tts": tts})
    with pytest.raises(ValueError, match=error):
        LocalKioskSpeech(_settings(), config)


def test_the_repo_config_speaks_arabic_with_mlx_whisper_and_mms() -> None:
    config = load_speech_config(PROVIDERS_CONFIG)
    assert (config.stt_provider("ar"), config.tts_provider("ar")) == ("mlx-whisper", "mms-tts-ara")
    assert (config.stt_provider("en"), config.tts_provider("en")) == ("mlx-whisper", "kokoro")


def _turn_config(turn: dict[str, Any]) -> SpeechConfig:
    return SpeechConfig.model_validate(
        {"stt": {"provider": "mlx-whisper"}, "tts": {"provider": "kokoro"}, "turn": turn}
    )


def test_each_language_ends_its_turn_with_its_own_settings(cohere_unlocked: list[str]) -> None:
    turn = {
        "en": {"smart_turn": False, "endpoint_silence_ms": 400},
        "ar": {"smart_turn": False, "endpoint_silence_ms": 700, "hard_silence_ms": 1200},
    }
    speech = LocalKioskSpeech(_settings(), _turn_config(turn))
    english, arabic = speech.stt("en"), speech.stt("ar")
    assert isinstance(english, MlxWhisperSTT) and isinstance(arabic, MlxWhisperSTT)
    assert english._semantic is None and arabic._semantic is None  # silence alone ends the turn
    assert english._endpoint_config.endpoint_silence_seconds == pytest.approx(0.4)
    assert arabic._endpoint_config.endpoint_silence_seconds == pytest.approx(0.7)
    assert arabic._endpoint_config.hard_silence_seconds == pytest.approx(1.2)

    config = _turn_config(turn).model_copy(
        update={"stt": _config(ar_stt="cohere-transcribe-arabic").stt}
    )
    cohere = LocalKioskSpeech(_settings(), config).stt("ar")
    assert type(cohere) is CohereTranscribeSTT and cohere._semantic is None
    assert cohere._endpoint_config.endpoint_silence_seconds == pytest.approx(0.7)


def test_a_language_without_turn_settings_keeps_the_services_smart_turn() -> None:
    settings = _settings()
    stt = LocalKioskSpeech(settings, _turn_config({})).stt("en")
    assert isinstance(stt, MlxWhisperSTT) and stt._semantic is not None
    assert stt._endpoint_config.semantic_check_silence_seconds == pytest.approx(
        settings.sherpa_stt_semantic_silence_ms / 1000
    )


@pytest.mark.parametrize(
    ("turn", "error"),
    [
        ({"fr": {"smart_turn": False}}, "unknown language"),
        (
            {"ar": {"smart_turn": False, "endpoint_silence_ms": 900, "hard_silence_ms": 800}},
            "hard_silence_ms",
        ),
        ({"en": {"semantic_check_silence_ms": 500, "endpoint_silence_ms": 400}}, "after the end"),
    ],
    ids=["unknown-language", "hard-before-end", "check-after-end"],
)
def test_inconsistent_turn_settings_fail_at_startup(turn: dict[str, Any], error: str) -> None:
    with pytest.raises(ValueError, match=error):
        LocalKioskSpeech(_settings(), _turn_config(turn))


def test_the_repo_config_ends_turns_on_silence_longer_in_arabic() -> None:
    config = load_speech_config(PROVIDERS_CONFIG)
    english, arabic = (
        config.turn_settings("en", _settings()),
        config.turn_settings("ar", _settings()),
    )
    assert english.smart_turn_model_path is None and english.endpoint_silence_ms == 400
    assert arabic.smart_turn_model_path is None and arabic.endpoint_silence_ms == 700
    assert arabic.hard_silence_ms == 1200
