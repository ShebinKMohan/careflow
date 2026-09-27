"""Arabic speech-to-text with Cohere Transcribe Arabic (M2-02), locally with transformers.

`CohereLabs/cohere-transcribe-arabic-07-2026` (2B parameters, BF16, Apache-2.0) is gated on
Hugging Face: it needs the owner's HF login and a one-time acceptance of its terms (a human step,
[M0-00]). The runtime never downloads it (4.1 GB): the pinned snapshot must already be in the local
Hugging Face cache, and when it is not, `model_info` says why and the service refuses to start
with this engine configured ("gated model, owner HF token needed"). It never falls back silently;
mlx-whisper stays the Arabic STT in config/providers.yaml until the owner unlocks the model.

Loading and inference follow the model card (transformers >= 5.4, no remote code): the processor
takes 16 kHz audio and `language="ar"`, and the model generates up to 256 tokens. The model runs on
MPS when available, else CPU, loads once per process on first use (in the STT worker thread) and
never auto-detects the language. The turn logic (Silero VAD, Smart Turn v3, pre-roll, the 30 s
cap) is `MlxWhisperSTT`'s; only the transcriber differs.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Final

from voice_agent.providers._memory import peak_rss_mb
from voice_agent.providers.mlx_whisper_stt import SAMPLE_RATE, MlxWhisperSTT, Transcriber

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

logger = logging.getLogger(__name__)

MODEL_ID: Final = "CohereLabs/cohere-transcribe-arabic-07-2026"
MODEL_REVISION: Final = "c3e911b42149bf7a1e53d5cef9878aee87515a23"
GATED_REASON: Final = "gated model, owner HF token needed"
MAX_NEW_TOKENS: Final = 256

_ENGINES: dict[tuple[str, str], tuple[Any, Any]] = {}
_ENGINES_LOCK = threading.Lock()


class CohereTranscribeUnavailableError(RuntimeError):
    """The model cannot be loaded here (gated, not downloaded, or transformers missing)."""


def ensure_accessible(model_id: str = MODEL_ID, revision: str = MODEL_REVISION) -> None:
    """Raise unless the pinned snapshot is in the local Hugging Face cache (never downloads)."""
    from huggingface_hub import hf_hub_download, model_info
    from huggingface_hub.errors import LocalEntryNotFoundError

    try:
        hf_hub_download(model_id, "config.json", revision=revision, local_files_only=True)
        return
    except LocalEntryNotFoundError:
        pass
    fetch = f"`hf download {model_id} --revision {revision}`"
    try:
        gated = bool(model_info(model_id, revision=revision).gated)
    except Exception as exc:  # noqa: BLE001 - offline or refused: the model is missing either way
        raise CohereTranscribeUnavailableError(
            f"{model_id} is not downloaded ({type(exc).__name__} from Hugging Face)"
        ) from exc
    reason = GATED_REASON if gated else "not downloaded"
    raise CohereTranscribeUnavailableError(
        f"{model_id} is not in the local Hugging Face cache ({reason}); fetch it with {fetch}"
    )


def _load(model_id: str, revision: str) -> tuple[Any, Any]:
    ensure_accessible(model_id, revision)
    try:
        import torch  # type: ignore[import-not-found]
        from transformers import (  # type: ignore[import-not-found]
            AutoProcessor,
            CohereAsrForConditionalGeneration,
        )
    except ImportError as exc:
        raise CohereTranscribeUnavailableError(
            "transformers and torch are not installed; install the voice 'arabic-speech' extra."
        ) from exc
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    processor = AutoProcessor.from_pretrained(model_id, revision=revision, local_files_only=True)
    model: Any = CohereAsrForConditionalGeneration.from_pretrained(
        model_id, revision=revision, local_files_only=True, dtype="auto"
    )
    return processor, model.to(device).eval()


def _cached_engine(model_id: str, revision: str) -> tuple[Any, Any]:
    key = (model_id, revision)
    with _ENGINES_LOCK:
        entry = _ENGINES.get(key)
        if entry is None:
            entry = _load(model_id, revision)
            _ENGINES[key] = entry
            logger.info(
                "cohere_stt.loaded",
                extra={"model": model_id, "revision": revision, "peak_rss_mb": peak_rss_mb()},
            )
        return entry


def cohere_transcriber(model_id: str = MODEL_ID, revision: str = MODEL_REVISION) -> Transcriber:
    """16 kHz float32 audio in, Arabic text out; the model loads on the first call."""

    def run(audio: NDArray[np.float32]) -> str:
        import torch  # type: ignore[import-not-found]

        processor, model = _cached_engine(model_id, revision)
        inputs = processor(audio, sampling_rate=SAMPLE_RATE, return_tensors="pt", language="ar")
        inputs = inputs.to(model.device, dtype=model.dtype)
        with torch.no_grad():
            outputs = model.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS)
        return str(processor.decode(outputs[0], skip_special_tokens=True))

    return run


def cohere_stt(
    *, vad_model_path: str, transcriber: Transcriber | None = None, **turn: Any
) -> MlxWhisperSTT:
    """The Arabic STT: `MlxWhisperSTT`'s turn logic with the Cohere transcriber, warmed once."""
    return MlxWhisperSTT(
        vad_model_path=vad_model_path,
        model=MODEL_ID,
        language="ar",
        transcriber=transcriber or cohere_transcriber(),
        warm=True,
        **turn,
    )


__all__ = [
    "GATED_REASON",
    "MODEL_ID",
    "MODEL_REVISION",
    "CohereTranscribeUnavailableError",
    "cohere_stt",
    "cohere_transcriber",
    "ensure_accessible",
]
