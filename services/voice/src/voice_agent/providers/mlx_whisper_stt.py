"""In-process speech-to-text: mlx-whisper (Apple silicon) + Silero VAD + smart-turn.

Whisper is not a streaming model, so the provider works turn by turn:

- 16 kHz PCM16 is buffered while the Silero VAD reports speech (with a short pre-roll so the
  first syllable is not clipped);
- the end of the turn is decided on the audio clock by the agent's `TurnEndpointTracker`: a
  smart-turn "is the sentence finished?" check after a short silence, a silence-only commit
  without it, and a hard silence cap;
- the whole turn is then converted to float32 and transcribed once, and one
  `TranscriptEvent(text, is_final=True, speech_final=True)` is emitted. There are no partials.

All engine work (VAD, smart-turn, transcription) runs on one worker thread, never on the event
loop: `accept_audio` only enqueues. Audio lives in memory only and is dropped after each turn.
mlx-whisper is imported lazily (the `mlx-whisper` extra, Apple silicon only); tests inject a
`transcriber`, a `vad_factory` and a `semantic_endpointer`.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from voice_agent.providers._silero_vad import VAD_FRAME_SAMPLES, SileroVoiceActivityDetector
from voice_agent.providers.base import TranscriptEvent
from voice_agent.providers.sherpa_stt import TurnEndpointConfig, TurnEndpointTracker
from voice_agent.providers.smart_turn import SmartTurnSemanticEndpointer

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"
DEFAULT_LANGUAGE = "en"
PRE_ROLL_SECONDS = 0.3
MAX_TURN_SECONDS = 30.0  # Whisper's window; a longer turn is committed at the cap
_AUDIO_QUEUE_MAX_SIZE = 1000
_TRANSCRIPT_QUEUE_MAX_SIZE = 20
_WORKER_JOIN_TIMEOUT_SECONDS = 5.0
_ENGINE_LOAD_TIMEOUT_SECONDS = 120.0
_SENTINEL = object()
_WARMED: set[str] = set()  # models already loaded and warmed in this process
_WARM_LOCK = threading.Lock()

Transcriber = Callable[["NDArray[np.float32]"], str]
"""16 kHz mono float32 audio in, text out."""


class VoiceActivity(Protocol):
    def reset(self) -> None: ...

    def process_frame(self, frame: NDArray[np.float32]) -> float: ...


class MlxWhisperUnavailableError(RuntimeError):
    """mlx-whisper is not installed or the model files are missing."""


def mlx_transcriber(model: str, language: str) -> Transcriber:
    """Transcribe with mlx-whisper; `model` is a local model directory or a Hugging Face repo.

    An absolute path must exist: CareFlow never lets mlx-whisper download on first use.
    """
    if Path(model).is_absolute() and not Path(model).is_dir():
        raise MlxWhisperUnavailableError(
            f"mlx-whisper model not found at {model}; run services/voice/scripts/download_models.sh"
        )
    try:
        import mlx_whisper  # type: ignore[import-not-found]
    except ImportError as exc:
        raise MlxWhisperUnavailableError(
            "mlx-whisper is not installed; install the 'mlx-whisper' extra (Apple silicon only)."
        ) from exc
    transcribe: Any = mlx_whisper.transcribe

    def run(audio: NDArray[np.float32]) -> str:
        result = transcribe(
            audio,
            path_or_hf_repo=model,
            language=language,
            condition_on_previous_text=False,
            verbose=None,
        )
        return str(result.get("text", "")) if isinstance(result, dict) else ""

    return run


def normalize_text(text: str) -> str:
    return " ".join(text.split())


class MlxWhisperSTT:
    """Turn-by-turn local STT implementing the `SpeechToText` protocol."""

    def __init__(
        self,
        *,
        vad_model_path: str,
        model: str = DEFAULT_MODEL,
        language: str = DEFAULT_LANGUAGE,
        smart_turn_model_path: str | None = None,
        smart_turn_threshold: float = 0.5,
        semantic_check_silence_ms: int = 200,
        endpoint_silence_ms: int = 400,
        hard_silence_ms: int = 900,
        transcriber: Transcriber | None = None,
        vad_factory: Callable[[], VoiceActivity] | None = None,
        semantic_endpointer: SmartTurnSemanticEndpointer | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        clean_vad_path = vad_model_path.strip()
        if not clean_vad_path and vad_factory is None:
            raise ValueError("vad_model_path must not be blank.")
        if not model.strip():
            raise ValueError("model must not be blank.")
        self._model = model.strip()
        self._language = language.strip() or DEFAULT_LANGUAGE
        self._endpoint_config = TurnEndpointConfig(
            semantic_check_silence_seconds=semantic_check_silence_ms / 1000.0,
            endpoint_silence_seconds=endpoint_silence_ms / 1000.0,
            hard_silence_seconds=hard_silence_ms / 1000.0,
        )
        clean_smart_turn = (smart_turn_model_path or "").strip() or None
        self._semantic = semantic_endpointer
        if self._semantic is None and clean_smart_turn is not None:
            self._semantic = SmartTurnSemanticEndpointer(
                model_path=clean_smart_turn, threshold=smart_turn_threshold
            )
        self._transcriber = transcriber
        self._vad_factory: Callable[[], VoiceActivity] = vad_factory or (
            lambda: SileroVoiceActivityDetector(model_path=clean_vad_path)
        )
        self._clock = clock
        self._audio: queue.Queue[object] = queue.Queue(maxsize=_AUDIO_QUEUE_MAX_SIZE)
        self._transcripts: asyncio.Queue[TranscriptEvent | None] = asyncio.Queue(
            maxsize=_TRANSCRIPT_QUEUE_MAX_SIZE
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._worker: threading.Thread | None = None
        self._worker_lock = threading.Lock()
        self._ready = threading.Event()
        self._failed = False
        self._closed = False

    async def start(self) -> None:
        """Load the engines before audio flows (StartableProvider); raises if they cannot load."""
        if self._closed:
            return
        self._loop = asyncio.get_running_loop()
        self._ensure_worker()
        await asyncio.to_thread(self._ready.wait, _ENGINE_LOAD_TIMEOUT_SECONDS)
        if self._failed:
            raise MlxWhisperUnavailableError("the speech-to-text engines failed to load")

    async def accept_audio(self, audio: bytes) -> TranscriptEvent | None:
        if self._closed:
            return None
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        self._ensure_worker()
        try:
            self._audio.put_nowait((audio, self._clock()))
        except queue.Full:
            logger.warning("Dropped an STT audio frame: the worker is behind.")
        return None

    async def transcripts(self) -> AsyncIterator[TranscriptEvent]:
        while True:
            event = await self._transcripts.get()
            if event is None:
                return
            yield event

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            worker = self._worker
            if worker is not None and worker.is_alive():
                while True:
                    try:
                        self._audio.put_nowait(_SENTINEL)
                        break
                    except queue.Full:
                        with suppress(queue.Empty):
                            self._audio.get_nowait()
                await asyncio.to_thread(worker.join, _WORKER_JOIN_TIMEOUT_SECONDS)
        self._emit(None)

    def _ensure_worker(self) -> None:
        if self._closed or self._failed:
            return
        with self._worker_lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._worker_main, name="mlx-whisper-stt", daemon=True
            )
            self._worker.start()

    def _emit(self, event: TranscriptEvent | None) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with suppress(RuntimeError):  # the loop is shutting down
            loop.call_soon_threadsafe(self._put_transcript, event)

    def _put_transcript(self, event: TranscriptEvent | None) -> None:
        with suppress(asyncio.QueueFull):
            self._transcripts.put_nowait(event)

    def _worker_main(self) -> None:
        try:
            self._run()
        except Exception:
            self._failed = True
            logger.exception("mlx-whisper STT worker failed; ending the transcript stream.")
            self._emit(None)
        finally:
            self._ready.set()

    def _run(self) -> None:
        import numpy as np

        transcribe = self._transcriber or mlx_transcriber(self._model, self._language)
        vad = self._vad_factory()
        vad.reset()
        semantic = self._semantic
        if semantic is not None:
            semantic.load()
        if self._transcriber is None:
            with _WARM_LOCK:
                if self._model not in _WARMED:  # load the model once per process
                    transcribe(np.zeros(SAMPLE_RATE // 2, dtype=np.float32))
                    _WARMED.add(self._model)
        self._ready.set()

        tracker = TurnEndpointTracker(self._endpoint_config, semantic_enabled=semantic is not None)
        pre_roll_samples = int(PRE_ROLL_SECONDS * SAMPLE_RATE)
        max_turn_samples = int(MAX_TURN_SECONDS * SAMPLE_RATE)
        pre_roll: list[NDArray[np.float32]] = []
        turn: list[NDArray[np.float32]] = []
        turn_samples = 0
        pending: NDArray[np.float32] = np.empty(0, dtype=np.float32)
        audio_time = 0.0
        speech_ended_at: float | None = None

        while True:
            item = self._audio.get()
            if item is _SENTINEL:
                return
            if not isinstance(item, tuple):
                continue
            payload, received_at = item
            if not isinstance(payload, bytes) or len(payload) < 2:
                continue
            usable = len(payload) - (len(payload) % 2)
            samples = np.frombuffer(payload[:usable], dtype="<i2").astype(np.float32) / 32768.0

            pending = np.concatenate([pending, samples])
            while len(pending) >= VAD_FRAME_SAMPLES:
                frame = pending[:VAD_FRAME_SAMPLES]
                pending = pending[VAD_FRAME_SAMPLES:]
                audio_time += VAD_FRAME_SAMPLES / SAMPLE_RATE
                was_speaking = tracker.is_speaking
                tracker.observe_frame(vad.process_frame(frame), audio_time)
                if tracker.speech_seen:
                    if not turn and pre_roll:
                        turn.extend(pre_roll)
                        turn_samples += sum(len(chunk) for chunk in pre_roll)
                        pre_roll.clear()
                    turn.append(frame)
                    turn_samples += len(frame)
                    if was_speaking and not tracker.is_speaking:
                        speech_ended_at = received_at
                else:
                    pre_roll.append(frame)
                    while sum(len(chunk) for chunk in pre_roll) > pre_roll_samples:
                        pre_roll.pop(0)

                if not tracker.speech_seen:
                    continue
                if tracker.wants_semantic_check(audio_time, has_text=True):
                    tracker.record_semantic_result(self._semantic_check(np.concatenate(turn)))
                over_cap = turn_samples >= max_turn_samples
                if over_cap or tracker.should_finalize(audio_time, has_text=True):
                    ended = speech_ended_at if speech_ended_at is not None else received_at
                    text = normalize_text(transcribe(np.concatenate(turn)))
                    if text:
                        self._emit(
                            TranscriptEvent(
                                text=text, is_final=True, speech_final=True, speech_ended_at=ended
                            )
                        )
                    tracker.reset_turn()
                    vad.reset()
                    turn.clear()
                    turn_samples = 0
                    speech_ended_at = None

    def _semantic_check(self, audio: NDArray[np.float32]) -> bool | None:
        semantic = self._semantic
        if semantic is None:
            return None
        try:
            return semantic.is_complete(audio)
        except Exception:
            logger.exception("smart-turn check failed; using the silence fallback.")
            return None
