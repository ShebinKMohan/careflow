"""Semantic end-of-turn classifier backed by the smart-turn v3 ONNX model.

smart-turn (pipecat-ai/smart-turn-v3, BSD 2-Clause, 8MB int8 ONNX) looks at the raw
waveform of the caller's current turn and predicts whether the utterance is complete.
Pairing it with a short VAD silence gate commits turns in ~200ms of silence when the
sentence is clearly finished, while holding through mid-sentence hesitations ("my
number is ... 555 ...") that fixed silence thresholds get wrong.

Heavy deps (numpy, onnxruntime) are imported lazily so this module can be imported by
the provider factory in environments without the ``local-speech`` extra installed.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

logger = logging.getLogger(__name__)

DEFAULT_COMPLETION_THRESHOLD = 0.5
_SESSION_CACHE: dict[tuple[str, int], Any] = {}
_SESSION_CACHE_LOCK = threading.Lock()


def _build_session(model_path: str, num_threads: int) -> Any:
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.inter_op_num_threads = 1
    options.intra_op_num_threads = num_threads
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(
        model_path, sess_options=options, providers=["CPUExecutionProvider"]
    )


def _cached_session(model_path: str, num_threads: int) -> Any:
    key = (model_path, num_threads)
    with _SESSION_CACHE_LOCK:
        session = _SESSION_CACHE.get(key)
        if session is None:
            session = _build_session(model_path, num_threads)
            _SESSION_CACHE[key] = session
        return session


class SmartTurnSemanticEndpointer:
    """Predicts end-of-turn completion probability from the turn's raw 16kHz audio.

    ``predict_completion`` is synchronous and CPU-bound (~10-30ms on Apple Silicon);
    callers run it on their worker thread, never on the event loop.
    """

    def __init__(
        self,
        *,
        model_path: str,
        threshold: float = DEFAULT_COMPLETION_THRESHOLD,
        num_threads: int = 1,
        session_factory: Callable[[], Any] | None = None,
    ) -> None:
        clean_model_path = model_path.strip()
        if not clean_model_path:
            raise ValueError("model_path must not be blank.")
        if not 0.0 < threshold < 1.0:
            raise ValueError("threshold must be between 0 and 1 (exclusive).")
        if num_threads < 1:
            raise ValueError("num_threads must be at least 1.")
        self._model_path = clean_model_path
        self._threshold = threshold
        self._num_threads = num_threads
        self._session_factory = session_factory
        self._session: Any | None = None
        self._session_lock = threading.Lock()

    @property
    def threshold(self) -> float:
        return self._threshold

    def load(self) -> None:
        """Eagerly load the ONNX session (otherwise loaded on first prediction)."""
        self._ensure_session()

    def _ensure_session(self) -> Any:
        with self._session_lock:
            if self._session is None:
                if self._session_factory is not None:
                    self._session = self._session_factory()
                else:
                    self._session = _cached_session(self._model_path, self._num_threads)
            return self._session

    def predict_completion(self, audio: NDArray[np.floating]) -> float:
        """Return the probability that the turn ending in ``audio`` is complete.

        ``audio`` is float 16kHz mono; only the trailing 8 seconds are used.
        """
        import numpy as np

        from voice_agent.providers._whisper_features import (
            WINDOW_SAMPLES,
            compute_whisper_log_mel_features,
        )

        session = self._ensure_session()
        window = np.asarray(audio, dtype=np.float32)
        if window.size > WINDOW_SAMPLES:
            window = window[-WINDOW_SAMPLES:]
        features = compute_whisper_log_mel_features(window)
        batch = np.expand_dims(features, axis=0)
        outputs = session.run(None, {"input_features": batch})
        # Despite the output name "logits", the smart-turn v3 ONNX graphs end in a
        # Sigmoid node, so the value is already a completion probability in (0, 1).
        # Re-applying sigmoid would compress everything into (0.5, 0.73) and make
        # every turn look complete at any threshold <= 0.5.
        probability = float(np.asarray(outputs[0]).reshape(-1)[0])
        return min(max(probability, 0.0), 1.0)

    def is_complete(self, audio: NDArray[np.floating]) -> bool:
        return self.predict_completion(audio) >= self._threshold
