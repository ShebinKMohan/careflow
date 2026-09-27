"""In-process CPU Arabic text-to-speech via MMS-TTS (`facebook/mms-tts-ara`, M2-02).

Meta's Massively Multilingual Speech VITS model for Arabic: ungated, about 290 MB, CC-BY-NC-4.0
(non-commercial; recorded in ADR-033). It replaced SILMA TTS, whose package pins numpy <= 1.26 and
cannot share this workspace (numpy 2.5). The tokenizer takes Arabic script directly (no
romanisation). On Apple silicon CPU it renders about 4 s of speech in under half a second.

The files come from `services/voice/scripts/download_models.sh` (pinned revision, sha256), and the
engine loads them from that directory only: it never downloads on first use. Like Kokoro, the
engine (model and tokenizer) is shared process-wide and loaded once, synthesis is serialised per
engine, and the text is split into sentence units that are yielded as they finish. Output is
resampled from 16 kHz to the channel's rate (24 kHz for the kiosk). Heavy imports (torch,
transformers, soxr) are lazy.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, Final

from voice_agent.providers._memory import peak_rss_mb
from voice_agent.telephony.plivo import PlivoAudioFormat

logger = logging.getLogger(__name__)

MODEL_ID: Final = "facebook/mms-tts-ara"
MODEL_REVISION: Final = "afba26a4b470f9ae64aeeea91b99f080dee410de"
DEFAULT_MAX_UNIT_CHARS: Final = 160
_WARMUP_TEXT: Final = "مرحبا."
# Sentence ends, Arabic question mark and semicolon included.
_SENTENCE_SPLIT: Final = re.compile(r"(?<=[.!?؟؛;:])\s+")

_ENGINES: dict[str, tuple[Any, Any, threading.Lock]] = {}
_ENGINES_LOCK = threading.Lock()
_WARMED: set[str] = set()


class MmsTextToSpeechError(RuntimeError):
    """The MMS engine cannot synthesize usable audio (or is not installed)."""


def _load(model_path: str) -> tuple[Any, Any]:
    if not Path(model_path).is_dir():
        raise MmsTextToSpeechError(
            f"MMS-TTS model not found at {model_path}; "
            "run services/voice/scripts/download_models.sh"
        )
    try:
        from transformers import AutoTokenizer, VitsModel  # type: ignore[import-not-found]
    except ImportError as exc:
        raise MmsTextToSpeechError(
            "transformers and torch are not installed; install the voice 'arabic-speech' extra."
        ) from exc
    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
    model = VitsModel.from_pretrained(model_path, local_files_only=True).eval()
    return model, tokenizer


def _cached_engine(model_path: str) -> tuple[Any, Any, threading.Lock]:
    with _ENGINES_LOCK:
        entry = _ENGINES.get(model_path)
        if entry is None:
            model, tokenizer = _load(model_path)
            entry = (model, tokenizer, threading.Lock())
            _ENGINES[model_path] = entry
            logger.info("mms_tts.loaded", extra={"model": model_path, "peak_rss_mb": peak_rss_mb()})
        return entry


def split_units(text: str, max_chars: int = DEFAULT_MAX_UNIT_CHARS) -> list[str]:
    """Sentence-sized units, so a long reply streams; an over-long sentence is cut at words."""
    clean = " ".join(text.split())
    units: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(clean) if clean else []:
        while len(sentence) > max_chars:
            cut = sentence.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            units.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if sentence:
            units.append(sentence)
    return units


class MmsTextToSpeech:
    """CPU-local Arabic TTS implementing the `TextToSpeech` protocol."""

    def __init__(
        self,
        *,
        model_path: str,
        max_unit_chars: int = DEFAULT_MAX_UNIT_CHARS,
        engine_factory: Callable[[], tuple[Any, Any, threading.Lock]] | None = None,
    ) -> None:
        if not model_path.strip():
            raise ValueError("model_path must not be blank.")
        if max_unit_chars <= 0:
            raise ValueError("max_unit_chars must be positive.")
        self._key = model_path
        self._max_unit_chars = max_unit_chars
        self._engine_factory = engine_factory or (lambda: _cached_engine(model_path))
        self._engine: tuple[Any, Any, threading.Lock] | None = None
        self._setup_lock = threading.Lock()

    async def start(self) -> None:
        """Load and warm the engine before the first turn."""
        await asyncio.to_thread(self._warm)

    def _ensure_engine(self) -> tuple[Any, Any, threading.Lock]:
        with self._setup_lock:
            if self._engine is None:
                self._engine = self._engine_factory()
            return self._engine

    def _warm(self) -> None:
        _, _, lock = self._ensure_engine()
        with lock:
            if self._key in _WARMED:
                return
        try:
            self._render(_WARMUP_TEXT, 16000, None)
        except Exception:
            logger.warning("MMS warm-up synthesis failed.", exc_info=True)
            return
        _WARMED.add(self._key)

    async def synthesize(
        self, *, text: str, audio_format: PlivoAudioFormat
    ) -> AsyncIterator[bytes]:
        if audio_format not in (PlivoAudioFormat.L16_16KHZ, PlivoAudioFormat.L16_24KHZ):
            raise ValueError("MMS TTS serves PCM16 16 kHz or 24 kHz streams.")
        units = split_units(text, self._max_unit_chars)
        if not units:
            raise MmsTextToSpeechError("MMS text must not be blank.")
        produced = False
        abort = threading.Event()
        try:
            for unit in units:
                pcm = await asyncio.to_thread(self._render, unit, audio_format.sample_rate, abort)
                if pcm:
                    produced = True
                    yield pcm
        except asyncio.CancelledError:
            abort.set()  # a barge-in: a queued unit returns without rendering
            raise
        if not produced:
            raise MmsTextToSpeechError("MMS returned no audio.")

    async def close(self) -> None:
        return None  # the engine is shared process-wide

    def _render(self, text: str, target_rate: int, abort: threading.Event | None) -> bytes:
        import numpy as np
        import torch  # type: ignore[import-not-found]

        model, tokenizer, lock = self._ensure_engine()
        with lock:
            if abort is not None and abort.is_set():
                return b""
            inputs = tokenizer(text, return_tensors="pt")
            if inputs["input_ids"].shape[-1] == 0:
                return b""
            with torch.no_grad():
                waveform = model(**inputs).waveform[0].cpu().numpy().astype(np.float32)
            native_rate = int(model.config.sampling_rate)
        if waveform.size == 0:
            return b""
        if native_rate != target_rate:
            import soxr

            resampled = soxr.resample(waveform, native_rate, target_rate)
            waveform = np.asarray(resampled, dtype=np.float32)
        return (np.clip(waveform, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


__all__ = ["MODEL_ID", "MODEL_REVISION", "MmsTextToSpeech", "MmsTextToSpeechError", "split_units"]
