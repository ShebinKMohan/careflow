"""Startup warms the common path without allocating Arabic's deferred speech model."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from voice_agent.careflow_app import _preload
from voice_agent.kiosk.speech import KioskSpeech


@pytest.mark.parametrize("failed_provider", [None, "stt", "tts"])
async def test_warmup_defers_arabic_and_releases_default_providers(
    failed_provider: str | None,
) -> None:
    speech = Mock(spec=KioskSpeech)
    stt = Mock(start=AsyncMock(), close=AsyncMock())
    english_tts = Mock(start=AsyncMock(), close=AsyncMock())
    arabic_tts = Mock(start=AsyncMock(), close=AsyncMock())
    speech.stt.return_value = stt
    speech.tts.side_effect = lambda language="en": arabic_tts if language == "ar" else english_tts
    if failed_provider:
        provider = stt if failed_provider == "stt" else english_tts
        provider.start.side_effect = RuntimeError("Model unavailable during warm-up")

    await _preload(speech)

    # Warm-up errors remain recoverable at connection time and do not prevent the
    # other common-path model from warming. Per-call resources are always released.
    stt.start.assert_awaited_once()
    english_tts.start.assert_awaited_once()
    stt.close.assert_awaited_once()
    english_tts.close.assert_awaited_once()
    speech.vad.return_value.reset.assert_called_once()
    assert all(call.args != ("ar",) for call in speech.tts.call_args_list)
    arabic_tts.start.assert_not_awaited()
    arabic_tts.close.assert_not_awaited()

    # Deferral preserves the Arabic factory for the first Arabic connection.
    assert speech.tts("ar") is arabic_tts
