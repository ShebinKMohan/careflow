"""Generate the synthetic test WAVs in services/voice/tests/fixtures/audio/ with Kokoro.

Synthetic speech only (never a real recording): 16 kHz mono PCM16, with leading and trailing
silence so a VAD sees a clean turn. Needs the Kokoro files from download_models.sh.

Usage: uv run python services/voice/scripts/make_fixture_audio.py
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import soxr
from kokoro_onnx import Kokoro

VOICE_ROOT = Path(__file__).resolve().parents[1]
MODELS = VOICE_ROOT / "models"
OUT = VOICE_ROOT / "tests" / "fixtures" / "audio"
RATE = 16000
LEAD_SECONDS = 0.5
TAIL_SECONDS = 1.5

FIXTURES = {
    "chest_pain.wav": "I have chest pain since this morning.",
}


def main() -> None:
    engine = Kokoro(str(MODELS / "kokoro-v1.0.onnx"), str(MODELS / "voices-v1.0.bin"))
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in FIXTURES.items():
        samples, native_rate = engine.create(text, voice="af_heart", speed=1.0, lang="en-us")
        speech = soxr.resample(np.asarray(samples, dtype=np.float32), int(native_rate), RATE)
        audio = np.concatenate(
            [
                np.zeros(int(LEAD_SECONDS * RATE), dtype=np.float32),
                np.asarray(speech, dtype=np.float32),
                np.zeros(int(TAIL_SECONDS * RATE), dtype=np.float32),
            ]
        )
        pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
        with wave.open(str(OUT / name), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(RATE)
            handle.writeframes(pcm.tobytes())
        print(f"{name}: {len(pcm) / RATE:.2f} s, {text!r}")


if __name__ == "__main__":
    main()
