"""Kiosk voice turn latency per language, on the real speech engines (M2-02).

    uv run --extra mlx-whisper --extra arabic-speech python eval/speech/run_latency.py

The voice service runs in this process with its real engines (`LocalKioskSpeech`: Silero VAD,
mlx-whisper, Kokoro for English, MMS-TTS for Arabic, and the configured end of turn). For each
language one kiosk session plays 20 clips of eval/datasets/speech/manifest.jsonl over the kiosk
audio WebSocket (`/kiosk/audio`) in real time, 20 ms a frame. After each answer it waits for the
spoken question to finish, as the kiosk does, so no answer barges in.

The voice service's own clock gives each stage (`GET /latency/summary`), from the end of speech
to the first byte of the spoken question: the end of turn and the transcription, the API, and the
TTS. The API is stubbed and answers at once with a next question in the session's language, so
**no model extraction is in these numbers**. With the local model, the extraction adds its own
time before the API answers (M2-12 measured 6 to 8 s for a typed answer). It writes the `latency`
section of eval/reports/speech.json. Measured on this Mac and reported, never a gate.
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
import sys
import time
import uuid
import wave
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from careflow_eval.speech import Clip, read_manifest, update_report

REPO: Final = Path(__file__).resolve().parents[2]
MANIFEST: Final = REPO / "eval" / "datasets" / "speech" / "manifest.jsonl"
REPORT: Final = REPO / "eval" / "reports" / "speech.json"
API: Final = "http://api.eval"
TURNS: Final = 20
FRAME_BYTES: Final = 640
QUESTIONS: Final = {
    "en": "When did this start?",
    "ar": "متى بدأ هذا؟",
}


def pick(clips: list[Clip], lang: str, count: int = TURNS) -> list[Clip]:
    """The hand-written sentences first, the dialects taking turns, then scenario turns."""
    sentences = [c for c in clips if c.lang == lang and c.origin == "sentences"]
    by_dialect: dict[str, list[Clip]] = {}
    for clip in sentences:
        by_dialect.setdefault(clip.dialect, []).append(clip)
    mixed: list[Clip] = []
    while any(by_dialect.values()):
        for queue in by_dialect.values():
            if queue:
                mixed.append(queue.pop(0))
    scenarios = [c for c in clips if c.lang == lang and c.origin != "sentences"]
    seen: set[str] = set()
    picked: list[Clip] = []
    for clip in [*mixed, *scenarios]:
        if clip.audio_path not in seen:
            seen.add(clip.audio_path)
            picked.append(clip)
    return picked[:count]


def pcm_of(clip: Clip) -> bytes:
    with wave.open(str(REPO / clip.audio_path), "rb") as handle:
        return handle.readframes(handle.getnframes())


def token(session_id: str, secret: str) -> str:
    import jwt

    now = dt.datetime.now(dt.UTC)
    claims = {
        "sub": session_id,
        "role": "patient_session",
        "clinic_id": str(uuid.UUID(int=1)),
        "session_id": session_id,
        "iat": now,
        "exp": now + dt.timedelta(minutes=30),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, secret, algorithm="HS256")


def session(lang: str, clips: list[Clip]) -> dict[str, Any]:
    """One kiosk voice session in `lang`: every clip an answer; the service's latency summary."""
    import respx
    from fastapi.testclient import TestClient
    from pydantic import SecretStr

    from voice_agent.careflow_app import create_careflow_app
    from voice_agent.config.careflow import CareflowSettings
    from voice_agent.kiosk.speech import LocalKioskSpeech

    secret = secrets.token_urlsafe(32)  # a fresh throwaway secret: no real token is involved
    session_id = f"ses_latency_{lang}"
    settings = CareflowSettings(careflow_jwt_secret=SecretStr(secret), careflow_api_base_url=API)
    app = create_careflow_app(settings, speech=LocalKioskSpeech(settings), preload=True)
    question = {"question_id": "q_onset", "text": QUESTIONS[lang], "key": "symptom_onset_at"}
    reply = {
        "turn_id": "turn_eval",
        "intake_revision": 1,
        "assistant_text": "",
        "done": False,
        "next_question": {**question, "notice": None},
    }
    consent = {"session_id": session_id, "camera": False, "voice": True, "data_use": True}
    consent |= {"consent_version": "2026-09-27.1", "recorded_at": "2026-09-28T00:00:00Z"}
    heard: list[dict[str, str]] = []
    with respx.mock(base_url=API, assert_all_called=False) as api, TestClient(app) as client:
        api.post(f"/v1/sessions/{session_id}/turns").respond(200, json=reply)
        api.get(f"/v1/sessions/{session_id}/consent").respond(200, json=consent)
        with client.websocket_connect("/kiosk/audio") as ws:
            start = {"type": "start", "session_id": session_id, "sample_rate": 16000}
            ws.send_text(
                json.dumps({**start, "language": lang, "token": token(session_id, secret)})
            )
            for clip in clips:
                pcm = pcm_of(clip)
                for offset in range(0, len(pcm), FRAME_BYTES):
                    ws.send_bytes(pcm[offset : offset + FRAME_BYTES])
                    time.sleep(0.02)  # real time
                audio, finals = 0, []
                while True:
                    message = ws.receive()
                    if message.get("bytes") is not None:
                        audio += len(message["bytes"])
                        continue
                    if message.get("text") is None:
                        raise RuntimeError(f"{clip.id}: the voice service closed: {message}")
                    parsed = json.loads(message["text"])
                    if parsed["type"] == "error":
                        raise RuntimeError(f"{clip.id}: {parsed}")
                    if parsed["type"] == "final":
                        finals.append(parsed["text"])
                    if parsed["type"] == "tts_end":
                        break
                heard.append({"id": clip.id, "heard": " | ".join(finals)})
                time.sleep(audio / 2 / 24000)  # the question plays out before the next answer
            ws.send_text(json.dumps({"type": "stop"}))
        summary: dict[str, Any] = client.get("/latency/summary").json()["kiosk"]
    return {"summary": summary, "heard": heard}


def row(
    lang: str, clips: list[Clip], result: dict[str, Any], engines: dict[str, str]
) -> dict[str, Any]:
    summary = result["summary"]
    stages = {
        name: {"p50_ms": stage["p50_ms"], "p95_ms": stage["p95_ms"], "max_ms": stage["max_ms"]}
        for name, stage in summary["stages"].items()
    }
    total = summary["stages"]["total"]
    return {
        "lang": lang,
        "stt": engines["stt"],
        "tts": engines["tts"],
        "llm": "none: the API is stubbed and answers at once (the extraction time is not included)",
        "n": summary["turn_count"],
        "answers": len(clips),
        "p50_ms": total["p50_ms"],
        "p95_ms": total["p95_ms"],
        "stages": stages,
        "clips": [clip.id for clip in clips],
        "split_answers": sum(" | " in h["heard"] for h in result["heard"]),
    }


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    from voice_agent.providers.mms_tts import MODEL_ID as MMS
    from voice_agent.providers.mms_tts import MODEL_REVISION as MMS_REV

    clips = read_manifest(MANIFEST)
    whisper = "mlx-whisper (mlx-community/whisper-large-v3-turbo, local files)"
    engines = {
        "en": {"stt": whisper, "tts": "kokoro (kokoro-v1.0.onnx, voice af_heart)"},
        "ar": {"stt": whisper, "tts": f"mms-tts-ara ({MMS}@{MMS_REV})"},
    }
    rows = []
    for lang in ("en", "ar"):
        picked = pick(clips, lang)
        result = session(lang, picked)
        rows.append(row(lang, picked, result, engines[lang]))
        latest = rows[-1]
        print(
            f"run_latency: {lang}: {latest['n']} turns from {latest['answers']} answers, "
            f"end of speech to first TTS byte p50 {latest['p50_ms']} ms, p95 {latest['p95_ms']} ms"
        )
    update_report(
        REPORT,
        "latency",
        {
            "method": (
                "the voice service in-process with its real engines; one kiosk session per "
                "language over /kiosk/audio in real time; the service's own stage timings from "
                "the end of speech to the first TTS byte; the API stubbed (no model extraction)"
            ),
            "rows": rows,
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
