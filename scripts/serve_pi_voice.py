"""Run the existing CareFlow voice channel with Whisper on an ARM Linux CPU.

Only the inference runtime changes. The kiosk protocol, consent, language,
turn detection, Kokoro/MMS synthesis and intake API remain the existing ones.
This entrypoint is explicit; the Mac's regular startup remains unchanged.
"""

from __future__ import annotations

import gc
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import uvicorn

from voice_agent.careflow_app import create_careflow_app
from voice_agent.config.careflow import CareflowSettings
from voice_agent.kiosk.speech import LocalKioskSpeech
from voice_agent.providers.mlx_whisper_stt import MlxWhisperSTT

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

_DECODE_LOCK = threading.Lock()


class CpuKioskSpeech(LocalKioskSpeech):
    def _mlx_whisper(self, language: str) -> MlxWhisperSTT:
        model_path = Path(os.environ["CAREFLOW_WHISPER_CPU_MODEL"])
        if not (model_path / "model.bin").is_file():
            raise FileNotFoundError(f"Whisper CPU model missing at {model_path}")

        def transcribe(audio: NDArray[np.float32]) -> str:
            from faster_whisper import WhisperModel

            # Release speech weights before Qwen handles the returned transcript.
            # Audio remains in memory, as on the original voice path.
            with _DECODE_LOCK:
                model = WhisperModel(
                    str(model_path),
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=2,
                    num_workers=1,
                    local_files_only=True,
                )
                try:
                    segments, _ = model.transcribe(
                        audio,
                        language=language,
                        condition_on_previous_text=False,
                        beam_size=5,
                        vad_filter=False,
                    )
                    return " ".join(segment.text.strip() for segment in segments).strip()
                finally:
                    del model
                    gc.collect()

        return MlxWhisperSTT(
            vad_model_path=self._settings.sherpa_stt_vad_model_path,
            model=str(model_path),
            language=language,
            transcriber=transcribe,
            **self._turn(language),
        )


if __name__ == "__main__":
    settings = CareflowSettings()
    uvicorn.run(
        create_careflow_app(settings, speech=CpuKioskSpeech(settings), preload=True),
        host="127.0.0.1",
        port=int(os.environ.get("VOICE_PORT", "8090")),
        timeout_graceful_shutdown=int(settings.shutdown_grace_seconds),
    )
