from typing import Any

import pytest

from voice_agent.providers._silero_vad import VAD_FRAME_SAMPLES, SileroVoiceActivityDetector
from voice_agent.providers.smart_turn import SmartTurnSemanticEndpointer

np = pytest.importorskip("numpy")
_whisper_features = pytest.importorskip("voice_agent.providers._whisper_features")


def test_log_mel_features_shape_for_short_exact_and_overlong_inputs() -> None:
    window = int(_whisper_features.WINDOW_SAMPLES)
    rng = np.random.default_rng(7)
    for num_samples in (1000, window, window + 5000):
        audio = (rng.standard_normal(num_samples) * 0.1).astype(np.float32)

        features = _whisper_features.compute_whisper_log_mel_features(audio)

        assert features.shape == (80, 800)
        assert features.dtype == np.float32


def test_log_mel_features_values_stay_in_clamped_scaled_range() -> None:
    rng = np.random.default_rng(11)
    audio = (rng.standard_normal(16000) * 0.1).astype(np.float32)

    features = _whisper_features.compute_whisper_log_mel_features(audio)

    peak = float(features.max())
    assert peak <= 2.5  # scaled log10 mel power of normalized audio stays small
    # The 8-decade floor clamp bounds the spread to exactly (8 / 4) = 2.0 scaled units.
    assert float(features.min()) >= peak - 2.0 - 1e-5


def test_log_mel_features_reject_two_dimensional_audio() -> None:
    with pytest.raises(ValueError, match="1-D audio"):
        _whisper_features.compute_whisper_log_mel_features(np.zeros((2, 400), dtype=np.float32))


class FakeCompletionSession:
    """ONNX-session stand-in asserting the smart-turn input contract.

    The real smart-turn v3 graphs end in a Sigmoid node, so ``run`` returns a
    completion probability (despite the output being named "logits").
    """

    def __init__(self, probability: float) -> None:
        self._probability = probability
        self.run_calls = 0

    def run(self, output_names: object, feeds: dict[str, Any]) -> list[Any]:
        assert output_names is None
        assert set(feeds) == {"input_features"}
        batch = feeds["input_features"]
        assert batch.shape == (1, 80, 800)
        assert batch.dtype == np.float32
        self.run_calls += 1
        return [np.array([[self._probability]], dtype=np.float32)]


def test_smart_turn_high_probability_predicts_completion() -> None:
    session = FakeCompletionSession(probability=0.93)
    endpointer = SmartTurnSemanticEndpointer(
        model_path="models/fake-smart-turn.onnx",
        session_factory=lambda: session,
    )
    audio = np.zeros(16000, dtype=np.float32)

    assert endpointer.predict_completion(audio) == pytest.approx(0.93, abs=1e-6)
    assert endpointer.is_complete(audio)
    assert session.run_calls == 2


def test_smart_turn_low_probability_predicts_incomplete() -> None:
    endpointer = SmartTurnSemanticEndpointer(
        model_path="models/fake-smart-turn.onnx",
        session_factory=lambda: FakeCompletionSession(probability=0.06),
    )
    audio = np.zeros(32000, dtype=np.float32)

    assert endpointer.predict_completion(audio) == pytest.approx(0.06, abs=1e-6)
    assert not endpointer.is_complete(audio)


def test_smart_turn_probability_is_never_resquashed() -> None:
    # A probability in (0.5, 1) must pass through unchanged: re-applying sigmoid
    # would compress it toward 0.73 and make every turn look complete.
    endpointer = SmartTurnSemanticEndpointer(
        model_path="models/fake-smart-turn.onnx",
        session_factory=lambda: FakeCompletionSession(probability=0.51),
    )
    assert endpointer.predict_completion(np.zeros(16000, dtype=np.float32)) == pytest.approx(
        0.51, abs=1e-6
    )


def test_smart_turn_clamps_out_of_range_outputs() -> None:
    endpointer = SmartTurnSemanticEndpointer(
        model_path="models/fake-smart-turn.onnx",
        session_factory=lambda: FakeCompletionSession(probability=1.5),
    )
    assert endpointer.predict_completion(np.zeros(16000, dtype=np.float32)) == 1.0


def test_smart_turn_threshold_is_respected() -> None:
    endpointer = SmartTurnSemanticEndpointer(
        model_path="models/fake-smart-turn.onnx",
        threshold=0.99,
        session_factory=lambda: FakeCompletionSession(probability=0.93),
    )

    assert endpointer.threshold == 0.99
    assert not endpointer.is_complete(np.zeros(16000, dtype=np.float32))


def test_smart_turn_constructor_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="model_path must not be blank"):
        SmartTurnSemanticEndpointer(model_path="   ")
    for threshold in (0.0, 1.0):
        with pytest.raises(ValueError, match="threshold must be between"):
            SmartTurnSemanticEndpointer(model_path="m.onnx", threshold=threshold)
    with pytest.raises(ValueError, match="num_threads must be at least 1"):
        SmartTurnSemanticEndpointer(model_path="m.onnx", num_threads=0)


class FakeVadSession:
    """ONNX-session stand-in recording the recurrent state it receives."""

    def __init__(self) -> None:
        self.received_states: list[tuple[Any, Any]] = []
        self._call_count = 0

    def run(self, output_names: object, feeds: dict[str, Any]) -> list[Any]:
        assert output_names is None
        assert feeds["x"].shape == (1, VAD_FRAME_SAMPLES)
        self.received_states.append((feeds["h"].copy(), feeds["c"].copy()))
        self._call_count += 1
        new_hidden = np.full((2, 1, 64), float(self._call_count), dtype=np.float32)
        new_cell = np.full((2, 1, 64), -float(self._call_count), dtype=np.float32)
        return [np.array([[0.7]], dtype=np.float32), new_hidden, new_cell]


def test_silero_vad_threads_recurrent_state_between_frames_and_reset() -> None:
    session = FakeVadSession()
    vad = SileroVoiceActivityDetector(
        model_path="models/fake-vad.onnx",
        session_factory=lambda: session,
    )
    frame = np.zeros(VAD_FRAME_SAMPLES, dtype=np.float32)

    assert vad.process_frame(frame) == pytest.approx(0.7)
    first_hidden, first_cell = session.received_states[0]
    assert not first_hidden.any()
    assert not first_cell.any()

    assert vad.process_frame(frame) == pytest.approx(0.7)
    second_hidden, second_cell = session.received_states[1]
    assert np.array_equal(second_hidden, np.full((2, 1, 64), 1.0, dtype=np.float32))
    assert np.array_equal(second_cell, np.full((2, 1, 64), -1.0, dtype=np.float32))

    vad.reset()
    vad.process_frame(frame)
    third_hidden, third_cell = session.received_states[2]
    assert not third_hidden.any()
    assert not third_cell.any()


def test_silero_vad_rejects_wrong_frame_shape() -> None:
    vad = SileroVoiceActivityDetector(
        model_path="models/fake-vad.onnx",
        session_factory=FakeVadSession,
    )

    with pytest.raises(ValueError, match="512 samples"):
        vad.process_frame(np.zeros(100, dtype=np.float32))


def test_silero_vad_constructor_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="model_path must not be blank"):
        SileroVoiceActivityDetector(model_path="   ")
    with pytest.raises(ValueError, match="num_threads must be at least 1"):
        SileroVoiceActivityDetector(model_path="m.onnx", num_threads=0)
