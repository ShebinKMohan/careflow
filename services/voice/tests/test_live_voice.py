"""Live voice on the dev Mac: real Silero VAD, smart-turn, mlx-whisper and Kokoro.

    CAREFLOW_LIVE_VOICE=1 uv run --extra mlx-whisper pytest services/voice -m live -s

Needs the model files from services/voice/scripts/download_models.sh. Skipped (not_run) without
the flag, on a machine without mlx-whisper, or when a model file is missing.
"""

from __future__ import annotations

import asyncio
import json
import os
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

API = "http://api.test"
SESSION = "ses_live"
SECRET = secrets.token_urlsafe(32)  # a fresh fake secret for each run
FRAME = 640
TURNS = 5


def _not_run_reason() -> str | None:
    if os.environ.get("CAREFLOW_LIVE_VOICE") != "1":
        return "set CAREFLOW_LIVE_VOICE=1 to run the live voice tests"
    try:
        import mlx_whisper  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        return "mlx-whisper is not installed (the 'mlx-whisper' extra, Apple silicon only)"
    settings = CareflowSettings()
    for path in (
        settings.sherpa_stt_vad_model_path,
        settings.sherpa_stt_smart_turn_model_path or "",
        settings.kokoro_model_path,
        settings.kokoro_voices_path,
        str(Path(settings.mlx_whisper_model) / "weights.safetensors"),
    ):
        if not Path(path).is_file():
            return f"model file missing: {path} (run services/voice/scripts/download_models.sh)"
    return None


REASON = _not_run_reason()
pytestmark = [pytest.mark.live, pytest.mark.skipif(REASON is not None, reason=REASON or "")]


def _live_settings() -> CareflowSettings:
    return CareflowSettings(careflow_jwt_secret=SecretStr(SECRET), careflow_api_base_url=API)


async def test_the_fixture_wav_transcribes_to_chest_pain(chest_pain_pcm: bytes) -> None:
    stt = LocalKioskSpeech(_live_settings()).stt()
    assert isinstance(stt, MlxWhisperSTT)
    await stt.start()

    async def feed() -> None:
        for offset in range(0, len(chest_pain_pcm), FRAME):
            await stt.accept_audio(chest_pain_pcm[offset : offset + FRAME])
            await asyncio.sleep(0.02)  # real time

    feeder = asyncio.create_task(feed())
    event = await asyncio.wait_for(anext(aiter(stt.transcripts())), timeout=30)
    arrived = time.monotonic()
    await feeder
    await stt.close()
    assert event.speech_ended_at is not None
    print(
        f"\nlive transcript: {event.text!r}; "
        f"end of speech to final transcript {1000 * (arrived - event.speech_ended_at):.0f} ms"
    )
    assert "chest pain" in event.text.lower()


def test_a_live_kiosk_session_reports_p50_p95_and_writes_nothing(
    chest_pain_pcm: bytes, patient_token: Callable[..., str], disk_writes: Any
) -> None:
    settings = _live_settings()
    app = create_careflow_app(settings, speech=LocalKioskSpeech(settings), preload=True)
    question = {
        "question_id": "q_symptom_onset_at_1",
        "text": "When did this start?",
        "key": "symptom_onset_at",
        "notice": None,
    }
    reply = {"turn_id": "turn_01", "assistant_text": "", "done": False, "next_question": question}
    transcripts: list[str] = []
    with (
        respx.mock(base_url=API, assert_all_called=False) as api,
        TestClient(app) as client,
    ):
        api.post(f"/v1/sessions/{SESSION}/turns").respond(200, json=reply)
        with disk_writes, client.websocket_connect("/kiosk/audio") as ws:
            ws.send_text(
                json.dumps(
                    {
                        "type": "start",
                        "session_id": SESSION,
                        "token": patient_token(SESSION, secret=SECRET),
                        "sample_rate": 16000,
                    }
                )
            )
            for _ in range(TURNS):
                for offset in range(0, len(chest_pain_pcm), FRAME):
                    ws.send_bytes(chest_pain_pcm[offset : offset + FRAME])
                    time.sleep(0.02)  # real time
                audio = 0
                while True:
                    message = ws.receive()
                    if message.get("bytes") is not None:
                        audio += len(message["bytes"])
                        continue
                    parsed = json.loads(message["text"])
                    if parsed["type"] == "final":
                        transcripts.append(parsed["text"])
                    if parsed["type"] == "error":
                        raise AssertionError(parsed)
                    if parsed["type"] == "tts_end":
                        break
                time.sleep(audio / 2 / 24000)  # let the question finish playing
            ws.send_text(json.dumps({"type": "stop"}))
        summary = client.get("/latency/summary").json()["kiosk"]

    print(f"\nlive kiosk transcripts: {transcripts}")
    print(f"live kiosk latency: {json.dumps(summary, indent=1)}")
    print(f"files opened while recording: {disk_writes.opened}; for writing: {disk_writes.paths}")
    assert len(transcripts) == TURNS
    assert all("chest pain" in text.lower() for text in transcripts)
    assert summary["turn_count"] == TURNS
    for stage in summary["stages"].values():
        assert stage["p50_ms"] is not None and stage["p95_ms"] is not None, stage
    assert disk_writes.paths == []
