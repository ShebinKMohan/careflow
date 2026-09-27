"""In-process streaming speech-to-text: sherpa-onnx Zipformer + Silero VAD + smart-turn.

This is the CPU-local replacement for cloud STT. Everything runs inside the agent
process — no network hop, no connect/reconnect latency, deterministic cost:

- sherpa-onnx streaming Zipformer transducer decodes partials frame-synchronously
  (~2-3ms of CPU per 80ms of audio on Apple Silicon, measured);
- Silero VAD tracks per-32ms-frame speech probability (~0.15ms/frame);
- an optional smart-turn v3 semantic endpointer classifies "is this sentence finished?"
  after a short silence gate, committing clearly-finished turns in ~200ms of silence
  while holding through mid-sentence hesitations up to a hard silence cap.

Event semantics mirror DeepgramStreamingSpeechToText: interim ``TranscriptEvent``s while
the caller speaks, then one final event with ``is_final=True, speech_final=True`` per
turn. All engine work happens on a dedicated worker thread; ``accept_audio`` only
enqueues, and results are handed back to the event loop thread-safely.

Heavy deps (numpy, sherpa-onnx, onnxruntime) are imported lazily so this module can be
imported by the provider factory without the ``local-speech`` extra installed.
Endpointing runs on the audio clock (samples fed), not wall time, so behavior is
deterministic under test and unaffected by scheduling jitter.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from voice_agent.providers._silero_vad import (
    VAD_FRAME_SAMPLES,
    SileroVoiceActivityDetector,
)
from voice_agent.providers.base import TranscriptEvent
from voice_agent.providers.smart_turn import SmartTurnSemanticEndpointer

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
DEFAULT_NUM_THREADS = 2
DEFAULT_SEMANTIC_CHECK_SILENCE_MS = 200
DEFAULT_ENDPOINT_SILENCE_MS = 400
DEFAULT_HARD_SILENCE_MS = 900
# Synthetic zeros fed at finalize so the Zipformer's chunked look-ahead flushes the
# last words. This is virtual audio decoded in a few ms — it adds NO real latency —
# and 800ms comfortably covers the model's chunk + right-context window.
DEFAULT_FLUSH_PADDING_MS = 800
DEFAULT_SPEECH_TRIGGER_PROBABILITY = 0.5
DEFAULT_SPEECH_RELEASE_PROBABILITY = 0.35
DEFAULT_TRANSCRIPT_QUEUE_MAX_SIZE = 100
DEFAULT_AUDIO_QUEUE_MAX_SIZE = 500
_TURN_AUDIO_WINDOW_SECONDS = 8.0
_WORKER_JOIN_TIMEOUT_SECONDS = 3.0
_ENGINE_LOAD_TIMEOUT_SECONDS = 30.0

_SENTINEL = object()

_RECOGNIZER_CACHE: dict[tuple[str, int], tuple[Any, threading.Lock]] = {}
_RECOGNIZER_CACHE_LOCK = threading.Lock()


class SherpaSpeechToTextError(RuntimeError):
    """Raised when the local sherpa STT engine cannot be constructed."""


@dataclass(frozen=True, slots=True)
class TurnEndpointConfig:
    """Silence gates (seconds, audio clock) for end-of-turn decisions.

    ``semantic_check_silence_seconds`` — silence before asking smart-turn whether the
    sentence is complete (complete -> commit right away).
    ``endpoint_silence_seconds`` — silence-only commit when no semantic model is
    configured or the semantic check failed.
    ``hard_silence_seconds`` — commit no matter what smart-turn said; the ceiling on
    how long a mid-sentence hesitation can hold the turn open.
    """

    semantic_check_silence_seconds: float = DEFAULT_SEMANTIC_CHECK_SILENCE_MS / 1000.0
    endpoint_silence_seconds: float = DEFAULT_ENDPOINT_SILENCE_MS / 1000.0
    hard_silence_seconds: float = DEFAULT_HARD_SILENCE_MS / 1000.0
    speech_trigger_probability: float = DEFAULT_SPEECH_TRIGGER_PROBABILITY
    speech_release_probability: float = DEFAULT_SPEECH_RELEASE_PROBABILITY

    def __post_init__(self) -> None:
        if self.semantic_check_silence_seconds <= 0:
            raise ValueError("semantic_check_silence_seconds must be positive.")
        if self.endpoint_silence_seconds < self.semantic_check_silence_seconds:
            raise ValueError("endpoint_silence_seconds must be >= semantic_check_silence_seconds.")
        if self.hard_silence_seconds < self.endpoint_silence_seconds:
            raise ValueError("hard_silence_seconds must be >= endpoint_silence_seconds.")
        if not 0.0 < self.speech_release_probability <= self.speech_trigger_probability < 1.0:
            raise ValueError(
                "Require 0 < speech_release_probability <= speech_trigger_probability < 1."
            )


class TurnEndpointTracker:
    """Pure audio-clock end-of-turn state machine (no engine dependencies).

    Fed one VAD probability per 32ms frame. Exposes three questions the worker asks
    after each frame: is the caller speaking, should we run the semantic check now, and
    should the turn be finalized now. Semantic results are reported back via
    ``record_semantic_result`` (None = check failed -> silence-only fallback).
    """

    def __init__(self, config: TurnEndpointConfig, *, semantic_enabled: bool) -> None:
        self._config = config
        self._semantic_enabled = semantic_enabled
        self._speaking = False
        self._speech_seen = False
        self._last_speech_at: float | None = None
        self._semantic_checked = False
        self._semantic_complete: bool | None = None

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    @property
    def speech_seen(self) -> bool:
        return self._speech_seen

    def observe_frame(self, speech_probability: float, frame_end_time: float) -> None:
        if self._speaking:
            if speech_probability < self._config.speech_release_probability:
                self._speaking = False
        elif speech_probability >= self._config.speech_trigger_probability:
            self._speaking = True
        if self._speaking:
            self._speech_seen = True
            self._last_speech_at = frame_end_time
            self._semantic_checked = False
            self._semantic_complete = None

    def trailing_silence(self, now: float) -> float | None:
        if not self._speech_seen or self._last_speech_at is None or self._speaking:
            return None
        return max(0.0, now - self._last_speech_at)

    def wants_semantic_check(self, now: float, *, has_text: bool) -> bool:
        if not self._semantic_enabled or self._semantic_checked or not has_text:
            return False
        silence = self.trailing_silence(now)
        return silence is not None and silence >= self._config.semantic_check_silence_seconds

    def record_semantic_result(self, complete: bool | None) -> None:
        self._semantic_checked = True
        self._semantic_complete = complete

    def should_finalize(self, now: float, *, has_text: bool) -> bool:
        if not has_text:
            return False
        silence = self.trailing_silence(now)
        if silence is None:
            return False
        if silence >= self._config.hard_silence_seconds:
            return True
        if self._semantic_enabled:
            if self._semantic_checked and self._semantic_complete is True:
                return True
            if self._semantic_checked and self._semantic_complete is None:
                return silence >= self._config.endpoint_silence_seconds
            # Semantic says incomplete (hold for the hard cap), or the check has not
            # fired yet (it fires before endpoint_silence by construction).
            return False
        return silence >= self._config.endpoint_silence_seconds

    def reset_turn(self) -> None:
        self._speaking = False
        self._speech_seen = False
        self._last_speech_at = None
        self._semantic_checked = False
        self._semantic_complete = None


def normalize_transcript_text(text: str) -> str:
    """Zipformer emits ALL-CAPS tokens; collapse whitespace and lowercase for the LLM."""
    return " ".join(text.split()).lower()


def _resolve_transducer_files(model_dir: str) -> tuple[str, str, str, str]:
    """Pick encoder/decoder/joiner/tokens from a sherpa streaming-zipformer model dir.

    Prefers int8 encoder+joiner (fastest on CPU) with the fp32 decoder (int8 decoders
    measurably hurt transducer search quality for negligible speed gain).
    """
    directory = Path(model_dir)
    if not directory.is_dir():
        raise SherpaSpeechToTextError(f"sherpa STT model directory does not exist: {directory}")

    def pick(pattern: str, *, prefer_int8: bool) -> Path:
        candidates = sorted(directory.glob(pattern))
        candidates = [path for path in candidates if path.suffix == ".onnx"]
        if not candidates:
            raise SherpaSpeechToTextError(f"No {pattern} model found in {directory}.")
        int8 = [path for path in candidates if "int8" in path.name]
        fp32 = [path for path in candidates if "int8" not in path.name]
        if prefer_int8:
            return (int8 or fp32)[0]
        return (fp32 or int8)[0]

    tokens = directory / "tokens.txt"
    if not tokens.is_file():
        raise SherpaSpeechToTextError(f"tokens.txt not found in {directory}.")
    encoder = pick("encoder*", prefer_int8=True)
    decoder = pick("decoder*", prefer_int8=False)
    joiner = pick("joiner*", prefer_int8=True)
    return str(encoder), str(decoder), str(joiner), str(tokens)


def _build_recognizer(model_dir: str, num_threads: int) -> Any:
    try:
        import sherpa_onnx
    except ImportError as exc:
        raise SherpaSpeechToTextError(
            "sherpa-onnx is not installed; install the 'local-speech' extra."
        ) from exc
    encoder, decoder, joiner, tokens = _resolve_transducer_files(model_dir)
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=tokens,
        encoder=encoder,
        decoder=decoder,
        joiner=joiner,
        num_threads=num_threads,
        sample_rate=SAMPLE_RATE,
        feature_dim=80,
        # Endpointing is owned by TurnEndpointTracker (VAD + smart-turn), which is
        # deterministic and semantically aware; sherpa's silence rules stay off.
        enable_endpoint_detection=False,
        decoding_method="greedy_search",
    )


def _cached_recognizer(model_dir: str, num_threads: int) -> tuple[Any, threading.Lock]:
    """Share one recognizer per (model_dir, threads) process-wide.

    Loading the Zipformer takes ~1.5s; per-call streams are cheap. Decode calls are
    serialized with the returned lock because concurrent decode_stream on one
    recognizer is not documented as thread-safe (cost: ~3ms per 80ms of audio).
    """
    key = (str(Path(model_dir).resolve()), num_threads)
    with _RECOGNIZER_CACHE_LOCK:
        entry = _RECOGNIZER_CACHE.get(key)
        if entry is None:
            entry = (_build_recognizer(model_dir, num_threads), threading.Lock())
            _RECOGNIZER_CACHE[key] = entry
        return entry


class _TurnAudioBuffer:
    """Rolling float32 buffer of the last ~8s of audio for the semantic endpointer."""

    def __init__(self, max_seconds: float = _TURN_AUDIO_WINDOW_SECONDS) -> None:
        self._max_samples = int(max_seconds * SAMPLE_RATE)
        self._chunks: list[Any] = []
        self._total = 0

    def append(self, samples: NDArray[np.float32]) -> None:
        self._chunks.append(samples)
        self._total += len(samples)
        while self._chunks and self._total - len(self._chunks[0]) >= self._max_samples:
            dropped = self._chunks.pop(0)
            self._total -= len(dropped)

    def snapshot(self) -> NDArray[np.float32]:
        import numpy as np

        if not self._chunks:
            return np.empty(0, dtype=np.float32)
        joined: NDArray[np.float32] = np.concatenate(self._chunks)
        if len(joined) > self._max_samples:
            joined = joined[-self._max_samples :]
        return joined

    def clear(self) -> None:
        self._chunks.clear()
        self._total = 0


class SherpaStreamingSpeechToText:
    """CPU-local streaming STT provider implementing the SpeechToText protocol."""

    def __init__(
        self,
        *,
        model_dir: str,
        vad_model_path: str,
        num_threads: int = DEFAULT_NUM_THREADS,
        semantic_check_silence_ms: int = DEFAULT_SEMANTIC_CHECK_SILENCE_MS,
        endpoint_silence_ms: int = DEFAULT_ENDPOINT_SILENCE_MS,
        hard_silence_ms: int = DEFAULT_HARD_SILENCE_MS,
        flush_padding_ms: int = DEFAULT_FLUSH_PADDING_MS,
        smart_turn_model_path: str | None = None,
        smart_turn_threshold: float = 0.5,
        transcript_queue_max_size: int = DEFAULT_TRANSCRIPT_QUEUE_MAX_SIZE,
        audio_queue_max_size: int = DEFAULT_AUDIO_QUEUE_MAX_SIZE,
        recognizer_factory: Callable[[], tuple[Any, threading.Lock]] | None = None,
        vad_factory: Callable[[], Any] | None = None,
        semantic_endpointer: SmartTurnSemanticEndpointer | None = None,
    ) -> None:
        clean_model_dir = model_dir.strip()
        if not clean_model_dir:
            raise ValueError("model_dir must not be blank.")
        clean_vad_path = vad_model_path.strip()
        if not clean_vad_path:
            raise ValueError("vad_model_path must not be blank.")
        if num_threads < 1:
            raise ValueError("num_threads must be at least 1.")
        if flush_padding_ms < 0:
            raise ValueError("flush_padding_ms must be non-negative.")
        if transcript_queue_max_size <= 0:
            raise ValueError("transcript_queue_max_size must be positive.")
        if audio_queue_max_size <= 0:
            raise ValueError("audio_queue_max_size must be positive.")
        self._model_dir = clean_model_dir
        self._vad_model_path = clean_vad_path
        self._num_threads = num_threads
        self._endpoint_config = TurnEndpointConfig(
            semantic_check_silence_seconds=semantic_check_silence_ms / 1000.0,
            endpoint_silence_seconds=endpoint_silence_ms / 1000.0,
            hard_silence_seconds=hard_silence_ms / 1000.0,
        )
        self._flush_padding_seconds = flush_padding_ms / 1000.0
        clean_smart_turn_path = (smart_turn_model_path or "").strip() or None
        self._semantic: SmartTurnSemanticEndpointer | None = semantic_endpointer
        if self._semantic is None and clean_smart_turn_path is not None:
            self._semantic = SmartTurnSemanticEndpointer(
                model_path=clean_smart_turn_path,
                threshold=smart_turn_threshold,
            )
        self._recognizer_factory = recognizer_factory or (
            lambda: _cached_recognizer(clean_model_dir, num_threads)
        )
        self._vad_factory = vad_factory or (
            lambda: SileroVoiceActivityDetector(model_path=clean_vad_path)
        )
        self._audio_queue: queue.Queue[object] = queue.Queue(maxsize=audio_queue_max_size)
        self._transcripts: asyncio.Queue[TranscriptEvent | None] = asyncio.Queue(
            maxsize=transcript_queue_max_size
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._worker: threading.Thread | None = None
        self._worker_lock = threading.Lock()
        self._engines_ready = threading.Event()
        self._worker_failed = False
        self._closed = False

    async def start(self) -> None:
        """Preload engines and start the worker before media flows (StartableProvider).

        Blocks until the worker thread has loaded its engines (recognizer, VAD,
        smart-turn), so the first caller utterance is never decoded by a cold engine.
        """
        if self._closed:
            return
        self._loop = asyncio.get_running_loop()
        self._ensure_worker()
        await asyncio.to_thread(self._engines_ready.wait, _ENGINE_LOAD_TIMEOUT_SECONDS)
        if not self._engines_ready.is_set():
            logger.warning("sherpa STT engines still loading after start() timeout.")

    async def accept_audio(self, audio: bytes) -> TranscriptEvent | None:
        if self._closed:
            return None
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        self._ensure_worker()
        self._enqueue_audio(audio)
        await asyncio.sleep(0)
        return None

    async def transcripts(self) -> AsyncIterator[TranscriptEvent]:
        while True:
            event = await self._transcripts.get()
            if event is None:
                return
            yield event

    async def close(self) -> None:
        if self._closed:
            self._emit_event(None)
            return
        self._closed = True
        worker = self._worker
        if worker is not None and worker.is_alive():
            # Never block the event loop on a full queue: drop-oldest until the
            # sentinel fits (accept_audio stops producing once _closed is set). A
            # full queue means the worker was already far behind real time, so the
            # dropped tail audio was stale anyway.
            while True:
                try:
                    self._audio_queue.put_nowait(_SENTINEL)
                    break
                except queue.Full:
                    with suppress(queue.Empty):
                        self._audio_queue.get_nowait()
            await asyncio.to_thread(worker.join, _WORKER_JOIN_TIMEOUT_SECONDS)
            if worker.is_alive():
                logger.warning("sherpa STT worker did not stop within join timeout.")
        self._emit_event(None)

    def _ensure_worker(self) -> None:
        if self._closed or self._worker_failed:
            # A crashed worker stays down for the life of this provider: respawning
            # per inbound 20ms frame would reload-and-crash ~50x/second. The session
            # already saw the transcript stream end and falls back accordingly.
            return
        with self._worker_lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._worker_loop,
                name="sherpa-stt-worker",
                daemon=True,
            )
            self._worker.start()

    def _enqueue_audio(self, audio: bytes) -> None:
        try:
            self._audio_queue.put_nowait(audio)
            return
        except queue.Full:
            with suppress(queue.Empty):
                self._audio_queue.get_nowait()
        try:
            self._audio_queue.put_nowait(audio)
        except queue.Full:
            logger.warning("Dropped sherpa STT audio frame because the queue is full.")

    def _emit_event(self, event: TranscriptEvent | None) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._enqueue_transcript, event)
        except RuntimeError:
            return  # loop already shut down

    def _enqueue_transcript(self, event: TranscriptEvent | None) -> None:
        while True:
            try:
                self._transcripts.put_nowait(event)
                return
            except asyncio.QueueFull:
                try:
                    dropped = self._transcripts.get_nowait()
                except asyncio.QueueEmpty:
                    continue
                if dropped is not None:
                    logger.warning("Dropped stale sherpa STT transcript event.")

    def _worker_loop(self) -> None:
        try:
            self._run_pipeline()
        except Exception:
            self._worker_failed = True
            logger.exception("sherpa STT worker crashed; ending transcript stream.")
            self._emit_event(None)
        finally:
            # Unblock a start() that is still waiting even if engine load failed.
            self._engines_ready.set()

    def _run_pipeline(self) -> None:
        import numpy as np

        recognizer, decode_lock = self._recognizer_factory()
        with decode_lock:
            stream = recognizer.create_stream()
        vad = self._vad_factory()
        vad.reset()
        semantic = self._semantic
        if semantic is not None:
            semantic.load()
        self._engines_ready.set()
        tracker = TurnEndpointTracker(
            self._endpoint_config,
            semantic_enabled=semantic is not None,
        )
        turn_audio = _TurnAudioBuffer()
        vad_pending: NDArray[np.float32] = np.empty(0, dtype=np.float32)
        audio_time = 0.0
        last_partial = ""

        # Drain until the sentinel: audio already accepted before close() is still
        # decoded (accept_audio refuses new frames once closed), so a fast producer
        # never loses its tail.
        while True:
            item = self._audio_queue.get()
            if item is _SENTINEL:
                return
            if not isinstance(item, bytes) or not item:
                continue
            usable = len(item) - (len(item) % 2)
            if usable == 0:
                continue
            samples = np.frombuffer(item[:usable], dtype="<i2").astype(np.float32) / 32768.0
            with decode_lock:
                stream.accept_waveform(SAMPLE_RATE, samples)
                while recognizer.is_ready(stream):
                    recognizer.decode_stream(stream)
                raw_text = recognizer.get_result(stream)
            text = normalize_transcript_text(raw_text)
            turn_audio.append(samples)

            vad_pending = np.concatenate([vad_pending, samples])
            while len(vad_pending) >= VAD_FRAME_SAMPLES:
                frame = vad_pending[:VAD_FRAME_SAMPLES]
                vad_pending = vad_pending[VAD_FRAME_SAMPLES:]
                audio_time += VAD_FRAME_SAMPLES / SAMPLE_RATE
                probability = vad.process_frame(frame)
                tracker.observe_frame(probability, audio_time)

            if text and text != last_partial:
                last_partial = text
                self._emit_event(TranscriptEvent(text=text, is_final=False, speech_final=False))

            has_text = bool(text)
            if tracker.wants_semantic_check(audio_time, has_text=has_text):
                tracker.record_semantic_result(self._run_semantic_check(turn_audio))

            if tracker.should_finalize(audio_time, has_text=has_text):
                final_text = self._flush_final_text(recognizer, stream, decode_lock)
                if final_text:
                    self._emit_event(
                        TranscriptEvent(
                            text=final_text,
                            is_final=True,
                            speech_final=True,
                        )
                    )
                tracker.reset_turn()
                turn_audio.clear()
                vad.reset()
                last_partial = ""

    def _run_semantic_check(self, turn_audio: _TurnAudioBuffer) -> bool | None:
        semantic = self._semantic
        if semantic is None:
            return None
        try:
            return semantic.is_complete(turn_audio.snapshot())
        except Exception:
            logger.exception("smart-turn semantic check failed; using silence fallback.")
            return None

    def _flush_final_text(
        self,
        recognizer: Any,
        stream: Any,
        decode_lock: threading.Lock,
    ) -> str:
        """Feed trailing silence so the Zipformer's look-ahead flushes the last words."""
        import numpy as np

        with decode_lock:
            if self._flush_padding_seconds > 0:
                padding = np.zeros(int(self._flush_padding_seconds * SAMPLE_RATE), dtype=np.float32)
                stream.accept_waveform(SAMPLE_RATE, padding)
                while recognizer.is_ready(stream):
                    recognizer.decode_stream(stream)
            text = normalize_transcript_text(recognizer.get_result(stream))
            recognizer.reset(stream)
        return text
