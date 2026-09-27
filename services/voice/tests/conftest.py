from __future__ import annotations

import datetime as dt
import os
import secrets
import sys
import threading
import uuid
import wave
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import jwt
import numpy as np
import pytest
from fastapi import FastAPI

from voice_agent.careflow_app import create_careflow_app
from voice_agent.config.settings import Settings
from voice_agent.kiosk.turns import TurnPoster
from voice_agent.providers.mlx_whisper_stt import MlxWhisperSTT
from voice_agent.telephony.plivo import PlivoAudioFormat

AUDIO = Path(__file__).parent / "fixtures" / "audio"
JWT_SECRET = secrets.token_urlsafe(32)  # a fresh fake secret for each test run
API = "http://api.test"
SPOKEN = "I have chest pain since this morning."


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --- audio and tokens ---


@pytest.fixture(scope="session")
def chest_pain_pcm() -> bytes:
    """The synthetic Kokoro WAV (scripts/make_fixture_audio.py): 16 kHz mono PCM16."""
    with wave.open(str(AUDIO / "chest_pain.wav"), "rb") as handle:
        assert (handle.getframerate(), handle.getnchannels(), handle.getsampwidth()) == (
            16000,
            1,
            2,
        )
        return handle.readframes(handle.getnframes())


@pytest.fixture
def patient_token() -> Callable[..., str]:
    def make(
        session_id: str = "ses_voice",
        *,
        role: str = "patient_session",
        secret: str = JWT_SECRET,
        expires_in: int = 600,
    ) -> str:
        now = dt.datetime.now(dt.UTC)
        claims = {
            "sub": session_id,
            "role": role,
            "clinic_id": str(uuid.UUID(int=1)),
            "session_id": session_id,
            "iat": now,
            "exp": now + dt.timedelta(seconds=expires_in),
            "jti": uuid.uuid4().hex,
        }
        return jwt.encode(claims, secret, algorithm="HS256")

    return make


# --- fake engines: the real turn logic runs, only the models are replaced -------------------------


class EnergyVad:
    """Speech when a 32 ms window is loud: deterministic on the synthetic fixture."""

    def __init__(self, threshold: float = 0.01) -> None:
        self.threshold = threshold

    def reset(self) -> None:
        return None

    def process_frame(self, frame: Any) -> float:
        rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))
        return 0.95 if rms > self.threshold else 0.02


class FakeTTS:
    """Deterministic PCM16 (a quiet tone) at the requested rate, in two chunks."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.spoken: list[str] = []
        self.formats: list[PlivoAudioFormat] = []

    async def synthesize(
        self, *, text: str, audio_format: PlivoAudioFormat
    ) -> AsyncIterator[bytes]:
        self.spoken.append(text)
        self.formats.append(audio_format)
        rate = audio_format.sample_rate
        samples = np.arange(int(self.seconds * rate))
        tone = (0.05 * np.sin(2 * np.pi * 220 * samples / rate) * 32767).astype("<i2").tobytes()
        half = len(tone) // 2 - (len(tone) // 2) % 2
        yield tone[:half]
        yield tone[half:]

    async def close(self) -> None:
        return None


class FakeSpeech:
    """A KioskSpeech whose STT is the real MlxWhisperSTT with a fake transcriber and VAD."""

    def __init__(self, *, text: str = SPOKEN, tts_seconds: float = 0.6) -> None:
        self.text = text
        self.tts_seconds = tts_seconds
        self.transcribed: list[Any] = []
        self.tts_engines: list[FakeTTS] = []

    def transcribe(self, audio: Any) -> str:
        self.transcribed.append(audio)
        return self.text

    def stt(self, language: str = "en") -> MlxWhisperSTT:
        return MlxWhisperSTT(
            vad_model_path="unused", vad_factory=EnergyVad, transcriber=self.transcribe
        )

    def tts(self, language: str = "en") -> FakeTTS:
        engine = FakeTTS(self.tts_seconds)
        self.tts_engines.append(engine)
        return engine

    def vad(self) -> EnergyVad:
        return EnergyVad()


@pytest.fixture
def fake_speech() -> type[FakeSpeech]:
    return FakeSpeech


@pytest.fixture
def energy_vad() -> type[EnergyVad]:
    return EnergyVad


@pytest.fixture
def fake_tts() -> type[FakeTTS]:
    return FakeTTS


def kiosk_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "session_backend": "memory",
        "stt_provider": "mlx-whisper",
        "llm_provider": "local",
        "tts_provider": "kokoro",
        "careflow_jwt_secret": JWT_SECRET,
        "careflow_api_base_url": API,
        "kiosk_start_timeout_seconds": 1.0,
        "kiosk_api_timeout_seconds": 2.0,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def make_kiosk_app() -> Callable[..., FastAPI]:
    def make(
        *,
        speech: Any | None = None,
        turns: TurnPoster | None = None,
        preload: bool = False,
        **settings: Any,
    ) -> FastAPI:
        return create_careflow_app(
            kiosk_settings(**settings), speech=speech or FakeSpeech(), turns=turns, preload=preload
        )

    return make


# --- the no-disk guarantee ---


class DiskWrites:
    """Files opened for writing (the `open` audit event) while recording."""

    _WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC

    def __init__(self) -> None:
        self.paths: list[str] = []
        self.opened = 0
        self._recording = False
        self._lock = threading.Lock()

    def hook(self, event: str, args: tuple[Any, ...]) -> None:
        if event != "open" or not self._recording:
            return
        path, mode, flags = (list(args) + [None, None, None])[:3]
        self.opened += 1
        writing = (isinstance(mode, str) and any(ch in mode for ch in "wax+")) or (
            isinstance(flags, int) and bool(flags & self._WRITE_FLAGS)
        )
        if writing:
            with self._lock:
                self.paths.append(str(path))

    def __enter__(self) -> DiskWrites:
        self.paths.clear()
        self.opened = 0
        self._recording = True
        return self

    def __exit__(self, *exc: object) -> None:
        self._recording = False


_DISK_WRITES = DiskWrites()
sys.addaudithook(_DISK_WRITES.hook)  # audit hooks cannot be removed; it records only when asked


@pytest.fixture
def disk_writes() -> Iterator[DiskWrites]:
    yield _DISK_WRITES
