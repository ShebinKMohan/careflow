"""Frame-level voice activity detection via the Silero VAD ONNX model.

Wraps the v4-style Silero graph (inputs ``x`` (1,512), LSTM state ``h``/``c``) at 16kHz:
one call per 512-sample (32ms) frame returns the speech probability and carries the
recurrent state forward. The ONNX session is shared process-wide (ort sessions are
thread-safe); the recurrent state lives per instance, one per call.

Heavy deps (numpy, onnxruntime) are imported lazily so the provider factory can import
this module in environments without the ``local-speech`` extra installed.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

VAD_FRAME_SAMPLES = 512  # 32ms at 16kHz — the window the Silero graph expects
_STATE_SHAPE = (2, 1, 64)
_SESSION_CACHE: dict[tuple[str, int], Any] = {}
_SESSION_CACHE_LOCK = threading.Lock()


def _build_session(model_path: str, num_threads: int) -> Any:
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.inter_op_num_threads = 1
    options.intra_op_num_threads = num_threads
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


class SileroVoiceActivityDetector:
    """Per-frame speech probability with hysteresis-friendly raw output.

    ``process_frame`` is synchronous and cheap (~0.15ms per frame on Apple Silicon);
    callers run it on their worker thread.
    """

    def __init__(
        self,
        *,
        model_path: str,
        num_threads: int = 1,
        session_factory: Callable[[], Any] | None = None,
    ) -> None:
        clean_model_path = model_path.strip()
        if not clean_model_path:
            raise ValueError("model_path must not be blank.")
        if num_threads < 1:
            raise ValueError("num_threads must be at least 1.")
        self._model_path = clean_model_path
        self._num_threads = num_threads
        self._session_factory = session_factory
        self._session: Any | None = None
        self._hidden: Any | None = None
        self._cell: Any | None = None

    def load(self) -> None:
        """Eagerly load the ONNX session (otherwise loaded on first frame)."""
        self._ensure_session()

    def _ensure_session(self) -> Any:
        if self._session is None:
            if self._session_factory is not None:
                self._session = self._session_factory()
            else:
                self._session = _cached_session(self._model_path, self._num_threads)
            self.reset()
        return self._session

    def reset(self) -> None:
        """Clear the recurrent state (call between utterances/calls, not per frame)."""
        import numpy as np

        self._hidden = np.zeros(_STATE_SHAPE, dtype=np.float32)
        self._cell = np.zeros(_STATE_SHAPE, dtype=np.float32)

    def process_frame(self, frame: NDArray[np.float32]) -> float:
        """Return speech probability for one 512-sample float32 frame at 16kHz."""
        import numpy as np

        session = self._ensure_session()
        if frame.shape != (VAD_FRAME_SAMPLES,):
            raise ValueError(
                f"Silero VAD expects exactly {VAD_FRAME_SAMPLES} samples per frame, "
                f"got shape {frame.shape}."
            )
        prob, self._hidden, self._cell = session.run(
            None,
            {
                "x": np.expand_dims(frame.astype(np.float32), axis=0),
                "h": self._hidden,
                "c": self._cell,
            },
        )
        return float(np.asarray(prob).reshape(-1)[0])
