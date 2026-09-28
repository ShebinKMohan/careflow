"""The real Arabic speech models on the dev Mac (M2-02), never in CI: MMS-TTS speaks Arabic that
mlx-whisper transcribes back, and an Arabic kiosk session runs on the real engines without writing
a file. The English engines are checked by test_live_voice.py.

    CAREFLOW_LIVE_VOICE=1 uv run --extra mlx-whisper --extra arabic-speech \\
        pytest services/voice -m models -s

Needs the model files from services/voice/scripts/download_models.sh. Skipped (not_run) without
the flag, on a machine without the extras, or when a model file is missing. The Arabic sentences
are synthetic, and machine-voiced audio is easier than a patient's voice: these checks prove the
engines run end to end, not an accuracy.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from voice_agent.careflow_app import create_careflow_app
from voice_agent.config.careflow import CareflowSettings
from voice_agent.kiosk.speech import LocalKioskSpeech
from voice_agent.providers.mlx_whisper_stt import MlxWhisperSTT
from voice_agent.providers.mms_tts import MmsTextToSpeech
from voice_agent.telephony.plivo import PlivoAudioFormat

API = "http://api.test"
SESSION = "ses_models_ar"
SECRET = secrets.token_urlsafe(32)  # a fresh fake secret for each run
FRAME = 640
RATE = 16000
SPOKEN = "عندي ألم في صدري من ساعة."
QUESTION = "هل ينتشر الألم إلى ذراعك؟"


def _not_run_reason() -> str | None:
    if os.environ.get("CAREFLOW_LIVE_VOICE") != "1":
        return "set CAREFLOW_LIVE_VOICE=1 to run the real speech models"
    for module in ("mlx_whisper", "torch", "transformers"):
        try:
            __import__(module)
        except ImportError:
            return f"{module} is not installed (the 'mlx-whisper' and 'arabic-speech' extras)"
    settings = CareflowSettings()
    for path in (
        settings.sherpa_stt_vad_model_path,
        settings.sherpa_stt_smart_turn_model_path or "",
        str(Path(settings.mlx_whisper_model) / "weights.safetensors"),
        str(Path(settings.mms_tts_model_path) / "model.safetensors"),
    ):
        if not Path(path).is_file():
            return f"model file missing: {path} (run services/voice/scripts/download_models.sh)"
    return None


REASON = _not_run_reason()
pytestmark = [
    pytest.mark.models,
    pytest.mark.live,
    pytest.mark.skipif(REASON is not None, reason=REASON or ""),
]

_DIACRITICS = re.compile("[\u064b-\u0652\u0640]")  # harakat and tatweel


def normalize_arabic(text: str) -> str:
    """The eval's Arabic normalisation (eval/speech/text.py), without punctuation."""
    text = _DIACRITICS.sub("", text)
    text = re.sub("[\u0623\u0625\u0622]", "\u0627", text).replace("\u0649", "\u064a")
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def _settings() -> CareflowSettings:
    return CareflowSettings(careflow_jwt_secret=SecretStr(SECRET), careflow_api_base_url=API)


async def _mms_pcm16(text: str) -> bytes:
    """MMS-TTS at 16 kHz with half a second of silence before and 1.5 s after, like a turn."""
    tts = MmsTextToSpeech(model_path=_settings().mms_tts_model_path)
    await tts.start()
    chunks = [c async for c in tts.synthesize(text=text, audio_format=PlivoAudioFormat.L16_16KHZ)]
    lead, tail = bytes(RATE), bytes(3 * RATE)  # PCM16: 2 bytes a sample
    return lead + b"".join(chunks) + tail


async def test_mms_arabic_speech_round_trips_through_mlx_whisper() -> None:
    pcm = await _mms_pcm16(SPOKEN)
    stt = LocalKioskSpeech(_settings()).stt("ar")
    assert isinstance(stt, MlxWhisperSTT) and stt._language == "ar"
    await stt.start()

    async def feed() -> None:
        for offset in range(0, len(pcm), FRAME):
            await stt.accept_audio(pcm[offset : offset + FRAME])
            await asyncio.sleep(0.02)  # real time

    feeder = asyncio.create_task(feed())
    event = await asyncio.wait_for(anext(aiter(stt.transcripts())), timeout=60)
    await feeder
    await stt.close()
    heard = normalize_arabic(event.text)
    print(f"\nMMS said {SPOKEN!r}; mlx-whisper heard {event.text!r}")
    assert event.is_final and event.speech_final
    assert "صدري" in heard and "الم" in heard  # "my chest", "pain": the complaint came through


def test_an_arabic_kiosk_session_on_the_real_engines_speaks_and_writes_nothing(
    patient_token: Callable[..., str], disk_writes: Any
) -> None:
    pcm = asyncio.run(_mms_pcm16(SPOKEN))
    settings = _settings()
    app = create_careflow_app(settings, speech=LocalKioskSpeech(settings), preload=True)
    question = {"question_id": "q_radiates", "text": QUESTION, "key": "chest_pain.radiates"}
    reply = {
        "turn_id": "turn_01",
        "intake_revision": 3,
        "assistant_text": "",
        "done": False,
        "next_question": {**question, "notice": None},
    }
    consent = {"session_id": SESSION, "camera": False, "voice": True, "data_use": True}
    consent |= {"consent_version": "2026-09-27.1", "recorded_at": "2026-09-27T09:00:00Z"}
    with respx.mock(base_url=API, assert_all_called=False) as api, TestClient(app) as client:
        turns = api.post(f"/v1/sessions/{SESSION}/turns").respond(200, json=reply)
        api.get(f"/v1/sessions/{SESSION}/consent").respond(200, json=consent)
        messages: list[dict[str, Any]] = []
        audio = 0
        with disk_writes, client.websocket_connect("/kiosk/audio") as ws:
            start = {"type": "start", "session_id": SESSION, "sample_rate": RATE, "language": "ar"}
            ws.send_text(json.dumps({**start, "token": patient_token(SESSION, secret=SECRET)}))
            for offset in range(0, len(pcm), FRAME):
                ws.send_bytes(pcm[offset : offset + FRAME])
                time.sleep(0.02)  # real time
            while True:
                message = ws.receive()
                if message.get("bytes") is not None:
                    audio += len(message["bytes"])
                    continue
                parsed = json.loads(message["text"])
                messages.append(parsed)
                if parsed["type"] == "error":
                    raise AssertionError(parsed)
                if parsed["type"] == "tts_end":
                    break
            ws.send_text(json.dumps({"type": "stop"}))
        summary = client.get("/latency/summary").json()["kiosk"]

    final = next(m["text"] for m in messages if m["type"] == "final")
    print(f"\nArabic kiosk session: heard {final!r}; {audio / 2 / 24000:.1f} s of MMS speech back")
    print(f"latency: {json.dumps(summary['stages']['total'])}")
    print(f"files opened while recording: {disk_writes.opened}; for writing: {disk_writes.paths}")
    assert "صدري" in normalize_arabic(final)
    [call] = turns.calls
    assert json.loads(call.request.content)["language"] == "ar"
    assert audio > 24000  # more than half a second of Arabic speech, PCM16 at 24 kHz
    assert disk_writes.paths == []  # no audio, transcript or other file written
