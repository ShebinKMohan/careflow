"""/readyz reports the kiosk speech model files (mlx-whisper, VAD, smart-turn, Kokoro)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

FILES = (
    "silero_vad.onnx",
    "smart-turn-v3.2-cpu.onnx",
    "kokoro-v1.0.onnx",
    "voices-v1.0.bin",
    "whisper-large-v3-turbo/config.json",
    "whisper-large-v3-turbo/weights.safetensors",
)


def _paths(models: Path) -> dict[str, str]:
    return {
        "sherpa_stt_vad_model_path": str(models / "silero_vad.onnx"),
        "sherpa_stt_smart_turn_model_path": str(models / "smart-turn-v3.2-cpu.onnx"),
        "kokoro_model_path": str(models / "kokoro-v1.0.onnx"),
        "kokoro_voices_path": str(models / "voices-v1.0.bin"),
        "mlx_whisper_model": str(models / "whisper-large-v3-turbo"),
    }


def test_missing_model_files_make_the_service_not_ready(
    tmp_path: Path, make_kiosk_app: Callable[..., FastAPI]
) -> None:
    with TestClient(make_kiosk_app(**_paths(tmp_path))) as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    missing = response.json()["provider_details"]["local_speech_models"]["missing"]
    assert sorted(Path(path).relative_to(tmp_path).as_posix() for path in missing) == sorted(FILES)


def test_all_model_files_present_is_ready(
    tmp_path: Path, make_kiosk_app: Callable[..., FastAPI]
) -> None:
    for name in FILES:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"x")
    with TestClient(make_kiosk_app(**_paths(tmp_path))) as client:
        response = client.get("/readyz")
    assert response.status_code == 200, response.json()
    assert response.json()["providers"] == {"stt": "mlx-whisper", "llm": "local", "tts": "kokoro"}
