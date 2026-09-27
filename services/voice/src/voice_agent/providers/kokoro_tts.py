"""In-process CPU text-to-speech via Kokoro-82M (kokoro-onnx).

The CPU-local replacement for network TTS. Kokoro-82M synthesizes at ~0.2x real-time
on Apple Silicon CPU (measured: a 2.4s spoken phrase takes ~550ms), so with the media
runner's phrase-level reply chunking the first audible chunk is ready in a few hundred
milliseconds and later chunks synthesize while earlier ones play. No network hop, no
per-request connection setup, deterministic latency.

Synthesis units within one ``synthesize`` call are split on sentence boundaries and
yielded as they finish, so long inputs stream instead of blocking until fully rendered.
Output is resampled 24kHz -> 16kHz mono PCM16 for Plivo L16 streams.

The engine (ONNX session + voice bank, ~340MB) is shared process-wide and loaded once;
synthesis calls are serialized per engine to bound CPU contention with the STT worker.
Heavy deps (numpy, kokoro-onnx, soxr) are imported lazily so this module can be
imported by the provider factory without the ``local-speech`` extra installed.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from collections.abc import AsyncIterator, Callable
from typing import Any

from voice_agent.telephony.plivo import PlivoAudioFormat

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "af_heart"
DEFAULT_SPEED = 1.0
DEFAULT_LANG = "en-us"
DEFAULT_MAX_UNIT_CHARS = 200
DEFAULT_FIRST_UNIT_MAX_CHARS = 20
KOKORO_NATIVE_SAMPLE_RATE = 24000

_ENGINE_CACHE: dict[tuple[str, str], tuple[Any, threading.Lock]] = {}
_ENGINE_CACHE_LOCK = threading.Lock()
_WARMED_ENGINES: set[tuple[str, str]] = set()
_WARMUP_TEXT = "Hi."

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;:])\s+")


class KokoroTextToSpeechError(RuntimeError):
    """Raised when the local Kokoro engine cannot synthesize usable audio."""


def _build_engine(model_path: str, voices_path: str) -> Any:
    try:
        from kokoro_onnx import Kokoro
    except ImportError as exc:
        raise KokoroTextToSpeechError(
            "kokoro-onnx is not installed; install the 'local-speech' extra."
        ) from exc
    return Kokoro(model_path, voices_path)


def _cached_engine(model_path: str, voices_path: str) -> tuple[Any, threading.Lock]:
    key = (model_path, voices_path)
    with _ENGINE_CACHE_LOCK:
        entry = _ENGINE_CACHE.get(key)
        if entry is None:
            entry = (_build_engine(model_path, voices_path), threading.Lock())
            _ENGINE_CACHE[key] = entry
        return entry


_MIN_SPLIT_TAIL_CHARS = 8


def _split_leading_unit(text: str, max_chars: int) -> tuple[str, str]:
    """Split a small leading unit off ``text`` at a comma, else a word boundary.

    Synthesis time scales with audio length, so a ~20-char first unit (~1.2s of
    speech) halves time-to-first-audio versus rendering a full 30-40 char phrase
    before any sound exists. Prefer a comma (natural prosodic break); fall back to
    the last word boundary within range. Text only slightly over the cap stays
    whole — a seam that buys almost no latency is not worth the prosody break, and
    a stranded few-character tail sounds worse than either.
    """
    if len(text) <= max_chars + _MIN_SPLIT_TAIL_CHARS:
        return text, ""
    window = text[: max_chars + 1]
    comma = window.rfind(",")
    if comma > 0:
        return text[: comma + 1].strip(), text[comma + 1 :].strip()
    space = window.rfind(" ")
    if space > 0:
        return text[:space].strip(), text[space:].strip()
    return text, ""


def split_synthesis_units(
    text: str,
    max_chars: int = DEFAULT_MAX_UNIT_CHARS,
    first_unit_max_chars: int | None = None,
) -> list[str]:
    """Split reply text into sentence-ish units so long inputs stream chunk by chunk.

    The media runner already feeds phrase-sized chunks; this is the second line of
    defense that keeps one oversized input from serializing into one long synthesis.
    With ``first_unit_max_chars`` set, the first unit is additionally capped so the
    first audible bytes are ready as early as possible.
    """
    clean = " ".join(text.split())
    if not clean:
        return []
    if first_unit_max_chars is not None and first_unit_max_chars > 0:
        # Cap only within the first sentence — a leading unit must never merge the
        # end of one sentence with the start of the next.
        parts = _SENTENCE_SPLIT_RE.split(clean, maxsplit=1)
        first_sentence = parts[0]
        remainder = parts[1] if len(parts) > 1 else ""
        head, rest = _split_leading_unit(first_sentence, first_unit_max_chars)
        tail = " ".join(part for part in (rest, remainder) if part)
        if len(head) > max_chars:
            # No comma/space inside the first-unit window (one unbroken token):
            # fall through to the standard splitter so the max_chars hard-wrap
            # still applies instead of one unbounded blocking synthesis.
            return split_synthesis_units(clean, max_chars)
        if tail:
            return [head, *split_synthesis_units(tail, max_chars)]
        return [head]
    sentences = _SENTENCE_SPLIT_RE.split(clean)
    units: list[str] = []
    for sentence in sentences:
        if len(sentence) <= max_chars:
            units.append(sentence)
            continue
        # No sentence boundary in range: split on commas, then hard-wrap on words.
        pending = ""
        for piece in re.split(r"(?<=,)\s+", sentence):
            candidate = f"{pending} {piece}".strip() if pending else piece
            if len(candidate) <= max_chars:
                pending = candidate
                continue
            if pending:
                units.append(pending)
            while len(piece) > max_chars:
                cut = piece.rfind(" ", 0, max_chars)
                if cut <= 0:
                    cut = max_chars
                units.append(piece[:cut].strip())
                piece = piece[cut:].strip()
            pending = piece
        if pending:
            units.append(pending)
    return [unit for unit in (u.strip() for u in units) if unit]


class KokoroTextToSpeech:
    """CPU-local streaming TTS provider implementing the TextToSpeech protocol."""

    def __init__(
        self,
        *,
        model_path: str,
        voices_path: str,
        voice: str = DEFAULT_VOICE,
        speed: float = DEFAULT_SPEED,
        lang: str = DEFAULT_LANG,
        max_unit_chars: int = DEFAULT_MAX_UNIT_CHARS,
        first_unit_max_chars: int = DEFAULT_FIRST_UNIT_MAX_CHARS,
        engine_factory: Callable[[], tuple[Any, threading.Lock]] | None = None,
    ) -> None:
        clean_model_path = model_path.strip()
        if not clean_model_path:
            raise ValueError("model_path must not be blank.")
        clean_voices_path = voices_path.strip()
        if not clean_voices_path:
            raise ValueError("voices_path must not be blank.")
        clean_voice = voice.strip()
        if not clean_voice:
            raise ValueError("voice must not be blank.")
        clean_lang = lang.strip()
        if not clean_lang:
            raise ValueError("lang must not be blank.")
        if speed <= 0:
            raise ValueError("speed must be positive.")
        if max_unit_chars <= 0:
            raise ValueError("max_unit_chars must be positive.")
        if first_unit_max_chars < 0:
            raise ValueError("first_unit_max_chars must be non-negative.")
        self._first_unit_max_chars = first_unit_max_chars
        self._model_path = clean_model_path
        self._voices_path = clean_voices_path
        self._voice = clean_voice
        self._speed = speed
        self._lang = clean_lang
        self._max_unit_chars = max_unit_chars
        self._engine_factory = engine_factory or (
            lambda: _cached_engine(clean_model_path, clean_voices_path)
        )
        self._engine: Any | None = None
        self._engine_lock: threading.Lock | None = None
        self._setup_lock = threading.Lock()

    async def start(self) -> None:
        """Preload and warm the engine before the first turn (StartableProvider).

        The first inference on a cold ONNX session costs ~1s extra (allocator and
        thread-pool warmup); one tiny throwaway synthesis per process absorbs that so
        the first caller-facing chunk gets warm-engine latency.
        """
        await asyncio.to_thread(self._ensure_warm_engine)

    def _ensure_warm_engine(self) -> None:
        engine, engine_lock = self._ensure_engine()
        key = (self._model_path, self._voices_path)
        with engine_lock:
            if key in _WARMED_ENGINES:
                return
            try:
                engine.create(_WARMUP_TEXT, voice=self._voice, speed=self._speed, lang=self._lang)
            except Exception:
                logger.warning("Kokoro warmup synthesis failed.", exc_info=True)
                return
            _WARMED_ENGINES.add(key)

    def _ensure_engine(self) -> tuple[Any, threading.Lock]:
        with self._setup_lock:
            if self._engine is None or self._engine_lock is None:
                self._engine, self._engine_lock = self._engine_factory()
            return self._engine, self._engine_lock

    async def synthesize(
        self,
        *,
        text: str,
        audio_format: PlivoAudioFormat,
    ) -> AsyncIterator[bytes]:
        if audio_format not in (PlivoAudioFormat.L16_16KHZ, PlivoAudioFormat.L16_24KHZ):
            raise ValueError(
                "Kokoro adapter is configured for PCM16 16kHz Plivo streams "
                "or PCM16 24kHz kiosk streams."
            )
        units = split_synthesis_units(
            text,
            self._max_unit_chars,
            first_unit_max_chars=self._first_unit_max_chars or None,
        )
        if not units:
            raise KokoroTextToSpeechError("Kokoro text must not be blank.")
        produced_audio = False
        # When the caller cancels (barge-in, hangup) while a unit is queued behind
        # the shared engine lock, the abort flag lets that orphaned thread return
        # without synthesizing instead of holding the lock through a full render
        # and delaying the next turn's first audio.
        abort = threading.Event()
        try:
            for unit in units:
                pcm = await asyncio.to_thread(
                    self._synthesize_unit, unit, audio_format.sample_rate, abort
                )
                if pcm:
                    produced_audio = True
                    yield pcm
        except asyncio.CancelledError:
            abort.set()
            raise
        if not produced_audio:
            raise KokoroTextToSpeechError("Kokoro returned no audio.")

    async def close(self) -> None:
        return None  # the engine is shared process-wide; nothing per-call to release

    def _synthesize_unit(
        self,
        text: str,
        target_sample_rate: int,
        abort: threading.Event | None = None,
    ) -> bytes:
        import numpy as np

        engine, engine_lock = self._ensure_engine()
        with engine_lock:
            if abort is not None and abort.is_set():
                return b""
            samples, sample_rate = engine.create(
                text,
                voice=self._voice,
                speed=self._speed,
                lang=self._lang,
            )
        waveform = np.asarray(samples, dtype=np.float32)
        if waveform.size == 0:
            return b""
        if int(sample_rate) != target_sample_rate:
            import soxr

            waveform = np.asarray(
                soxr.resample(waveform, int(sample_rate), target_sample_rate),
                dtype=np.float32,
            )
        clipped = np.clip(waveform, -1.0, 1.0)
        pcm16 = (clipped * 32767.0).astype("<i2")
        return pcm16.tobytes()
