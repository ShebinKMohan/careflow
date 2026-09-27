import asyncio
import struct
import threading
from pathlib import Path
from typing import Any

import pytest

from voice_agent.providers._silero_vad import VAD_FRAME_SAMPLES
from voice_agent.providers.base import TranscriptEvent
from voice_agent.providers.sherpa_stt import (
    SherpaSpeechToTextError,
    SherpaStreamingSpeechToText,
    TurnEndpointConfig,
    TurnEndpointTracker,
    _resolve_transducer_files,
    _TurnAudioBuffer,
    normalize_transcript_text,
)
from voice_agent.providers.smart_turn import SmartTurnSemanticEndpointer

# Gates used across the tracker decision-table tests (seconds, audio clock).
_CONFIG = TurnEndpointConfig(
    semantic_check_silence_seconds=0.2,
    endpoint_silence_seconds=0.4,
    hard_silence_seconds=0.9,
)


def _pcm_frame(value: int = 0) -> bytes:
    """One 512-sample (32ms at 16kHz) PCM16 frame — exactly one VAD frame."""
    return struct.pack("<h", value) * VAD_FRAME_SAMPLES


def test_turn_endpoint_config_rejects_non_positive_semantic_gate() -> None:
    with pytest.raises(ValueError, match="semantic_check_silence_seconds must be positive"):
        TurnEndpointConfig(semantic_check_silence_seconds=0.0)


def test_turn_endpoint_config_rejects_endpoint_gate_below_semantic_gate() -> None:
    with pytest.raises(ValueError, match="endpoint_silence_seconds must be >="):
        TurnEndpointConfig(
            semantic_check_silence_seconds=0.4,
            endpoint_silence_seconds=0.3,
        )


def test_turn_endpoint_config_rejects_hard_cap_below_endpoint_gate() -> None:
    with pytest.raises(ValueError, match="hard_silence_seconds must be >="):
        TurnEndpointConfig(endpoint_silence_seconds=0.5, hard_silence_seconds=0.4)


def test_turn_endpoint_config_rejects_bad_probability_ordering() -> None:
    with pytest.raises(ValueError, match="speech_release_probability"):
        TurnEndpointConfig(speech_trigger_probability=0.5, speech_release_probability=0.6)
    with pytest.raises(ValueError, match="speech_release_probability"):
        TurnEndpointConfig(speech_release_probability=0.0)
    with pytest.raises(ValueError, match="speech_release_probability"):
        TurnEndpointConfig(speech_trigger_probability=1.0)


def test_tracker_speech_trigger_and_release_hysteresis() -> None:
    tracker = TurnEndpointTracker(_CONFIG, semantic_enabled=False)

    assert not tracker.is_speaking
    tracker.observe_frame(0.49, 0.032)
    assert not tracker.is_speaking
    assert not tracker.speech_seen

    tracker.observe_frame(0.5, 0.064)
    assert tracker.is_speaking
    assert tracker.speech_seen

    # Between release (0.35) and trigger (0.5) the tracker keeps speaking.
    tracker.observe_frame(0.4, 0.096)
    assert tracker.is_speaking

    tracker.observe_frame(0.34, 0.128)
    assert not tracker.is_speaking
    assert tracker.trailing_silence(0.128) == pytest.approx(0.032)


def test_tracker_never_finalizes_without_text() -> None:
    tracker = TurnEndpointTracker(_CONFIG, semantic_enabled=False)
    tracker.observe_frame(0.9, 0.032)
    tracker.observe_frame(0.1, 0.064)

    assert not tracker.should_finalize(5.0, has_text=False)
    assert tracker.should_finalize(5.0, has_text=True)


def test_tracker_never_finalizes_while_speaking() -> None:
    tracker = TurnEndpointTracker(_CONFIG, semantic_enabled=False)
    tracker.observe_frame(0.9, 0.032)

    assert tracker.trailing_silence(5.0) is None
    assert not tracker.should_finalize(5.0, has_text=True)


def test_tracker_wants_semantic_check_only_after_gate_with_text() -> None:
    tracker = TurnEndpointTracker(_CONFIG, semantic_enabled=True)
    tracker.observe_frame(0.9, 0.1)
    tracker.observe_frame(0.1, 0.15)

    assert not tracker.wants_semantic_check(0.25, has_text=True)  # 0.15s silence < 0.2s gate
    assert not tracker.wants_semantic_check(0.4, has_text=False)  # no text -> never
    assert tracker.wants_semantic_check(0.31, has_text=True)  # 0.21s silence passes the gate


def test_tracker_semantic_check_fires_once_per_silence_stretch() -> None:
    tracker = TurnEndpointTracker(_CONFIG, semantic_enabled=True)
    tracker.observe_frame(0.9, 0.1)
    tracker.observe_frame(0.1, 0.15)

    assert tracker.wants_semantic_check(0.4, has_text=True)
    tracker.record_semantic_result(False)
    assert not tracker.wants_semantic_check(0.5, has_text=True)

    # Speech resumes -> the stale verdict is discarded and the check re-arms.
    tracker.observe_frame(0.9, 0.6)
    tracker.observe_frame(0.1, 0.65)
    assert not tracker.wants_semantic_check(0.7, has_text=True)  # 0.1s silence < gate
    assert tracker.wants_semantic_check(0.85, has_text=True)


def test_tracker_semantic_complete_finalizes_before_endpoint_gate() -> None:
    tracker = TurnEndpointTracker(_CONFIG, semantic_enabled=True)
    tracker.observe_frame(0.9, 0.1)
    tracker.observe_frame(0.1, 0.15)

    assert not tracker.should_finalize(0.3, has_text=True)  # unchecked -> hold
    tracker.record_semantic_result(True)
    assert tracker.should_finalize(0.3, has_text=True)  # 0.2s silence << 0.4s endpoint gate


def test_tracker_semantic_incomplete_holds_until_hard_cap() -> None:
    tracker = TurnEndpointTracker(_CONFIG, semantic_enabled=True)
    tracker.observe_frame(0.9, 0.1)
    tracker.observe_frame(0.1, 0.15)
    tracker.record_semantic_result(False)

    assert not tracker.should_finalize(0.5, has_text=True)  # past the 0.4s endpoint gate
    assert not tracker.should_finalize(0.99, has_text=True)  # 0.89s silence < 0.9s cap
    assert tracker.should_finalize(1.0, has_text=True)  # hard cap always wins


def test_tracker_failed_semantic_check_falls_back_to_endpoint_gate() -> None:
    tracker = TurnEndpointTracker(_CONFIG, semantic_enabled=True)
    tracker.observe_frame(0.9, 0.1)
    tracker.observe_frame(0.1, 0.15)
    tracker.record_semantic_result(None)

    assert not tracker.should_finalize(0.45, has_text=True)  # 0.35s silence < 0.4s gate
    assert tracker.should_finalize(0.5, has_text=True)


def test_tracker_without_semantic_finalizes_at_endpoint_gate() -> None:
    tracker = TurnEndpointTracker(_CONFIG, semantic_enabled=False)
    tracker.observe_frame(0.9, 0.1)
    tracker.observe_frame(0.1, 0.15)

    assert not tracker.wants_semantic_check(1.0, has_text=True)
    assert not tracker.should_finalize(0.45, has_text=True)
    assert tracker.should_finalize(0.5, has_text=True)


def test_tracker_hard_cap_finalizes_even_without_semantic_verdict() -> None:
    tracker = TurnEndpointTracker(_CONFIG, semantic_enabled=True)
    tracker.observe_frame(0.9, 0.1)
    tracker.observe_frame(0.1, 0.15)

    assert not tracker.should_finalize(0.9, has_text=True)  # 0.8s silence, unchecked -> hold
    assert tracker.should_finalize(1.0, has_text=True)


def test_tracker_reset_turn_clears_all_state() -> None:
    tracker = TurnEndpointTracker(_CONFIG, semantic_enabled=True)
    tracker.observe_frame(0.9, 0.1)
    tracker.observe_frame(0.1, 0.15)
    tracker.record_semantic_result(True)

    tracker.reset_turn()

    assert not tracker.is_speaking
    assert not tracker.speech_seen
    assert tracker.trailing_silence(5.0) is None
    assert not tracker.should_finalize(5.0, has_text=True)
    assert not tracker.wants_semantic_check(5.0, has_text=True)


def test_normalize_transcript_text_lowercases_and_collapses_whitespace() -> None:
    assert normalize_transcript_text("  HELLO   WORLD\n\tAGAIN ") == "hello world again"
    assert normalize_transcript_text("") == ""


def _write_model_dir(tmp_path: Path) -> Path:
    model_dir = tmp_path / "zipformer"
    model_dir.mkdir()
    for name in (
        "tokens.txt",
        "encoder-epoch-99-avg-1.onnx",
        "encoder-epoch-99-avg-1.int8.onnx",
        "decoder-epoch-99-avg-1.onnx",
        "decoder-epoch-99-avg-1.int8.onnx",
        "joiner-epoch-99-avg-1.onnx",
        "joiner-epoch-99-avg-1.int8.onnx",
    ):
        (model_dir / name).write_bytes(b"stub")
    return model_dir


def test_resolve_transducer_files_prefers_int8_encoder_joiner_fp32_decoder(
    tmp_path: Path,
) -> None:
    model_dir = _write_model_dir(tmp_path)

    encoder, decoder, joiner, tokens = _resolve_transducer_files(str(model_dir))

    assert encoder.endswith("encoder-epoch-99-avg-1.int8.onnx")
    assert decoder.endswith("decoder-epoch-99-avg-1.onnx")
    assert joiner.endswith("joiner-epoch-99-avg-1.int8.onnx")
    assert tokens.endswith("tokens.txt")


def test_resolve_transducer_files_rejects_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(SherpaSpeechToTextError, match="does not exist"):
        _resolve_transducer_files(str(tmp_path / "missing"))


def test_resolve_transducer_files_rejects_missing_tokens_file(tmp_path: Path) -> None:
    model_dir = _write_model_dir(tmp_path)
    (model_dir / "tokens.txt").unlink()

    with pytest.raises(SherpaSpeechToTextError, match=r"tokens\.txt"):
        _resolve_transducer_files(str(model_dir))


def test_resolve_transducer_files_rejects_missing_encoder(tmp_path: Path) -> None:
    model_dir = _write_model_dir(tmp_path)
    (model_dir / "encoder-epoch-99-avg-1.onnx").unlink()
    (model_dir / "encoder-epoch-99-avg-1.int8.onnx").unlink()

    with pytest.raises(SherpaSpeechToTextError, match="No encoder"):
        _resolve_transducer_files(str(model_dir))


def test_constructor_rejects_blank_and_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="model_dir must not be blank"):
        SherpaStreamingSpeechToText(model_dir="   ", vad_model_path="vad.onnx")
    with pytest.raises(ValueError, match="vad_model_path must not be blank"):
        SherpaStreamingSpeechToText(model_dir="models/x", vad_model_path="   ")
    with pytest.raises(ValueError, match="num_threads must be at least 1"):
        SherpaStreamingSpeechToText(model_dir="models/x", vad_model_path="vad.onnx", num_threads=0)
    with pytest.raises(ValueError, match="transcript_queue_max_size must be positive"):
        SherpaStreamingSpeechToText(
            model_dir="models/x", vad_model_path="vad.onnx", transcript_queue_max_size=0
        )
    with pytest.raises(ValueError, match="audio_queue_max_size must be positive"):
        SherpaStreamingSpeechToText(
            model_dir="models/x", vad_model_path="vad.onnx", audio_queue_max_size=0
        )


def test_turn_audio_buffer_keeps_only_the_recent_window() -> None:
    np = pytest.importorskip("numpy")
    buffer = _TurnAudioBuffer(max_seconds=0.01)  # 160 samples at 16kHz

    for start in range(0, 400, 100):
        buffer.append(np.arange(start, start + 100, dtype=np.float32))
    snapshot = buffer.snapshot()

    assert len(snapshot) == 160
    assert snapshot[0] == pytest.approx(240.0)
    assert snapshot[-1] == pytest.approx(399.0)

    buffer.clear()
    assert len(buffer.snapshot()) == 0


class FakeStream:
    """Duck-typed sherpa online stream recording the waveforms it was fed."""

    def __init__(self) -> None:
        self.accepted: list[tuple[int, Any]] = []

    def accept_waveform(self, sample_rate: int, samples: Any) -> None:
        self.accepted.append((sample_rate, samples))


class FakeRecognizer:
    """Duck-typed sherpa OnlineRecognizer with scripted growing results."""

    def __init__(self, results: list[str]) -> None:
        self._results = list(results)
        self._result_index = 0
        self._ready = False
        self.reset_calls = 0
        self.streams: list[FakeStream] = []

    def create_stream(self) -> FakeStream:
        stream = FakeStream()
        self.streams.append(stream)
        return stream

    def is_ready(self, stream: FakeStream) -> bool:
        del stream
        self._ready = not self._ready
        return self._ready

    def decode_stream(self, stream: FakeStream) -> None:
        del stream

    def get_result(self, stream: FakeStream) -> str:
        del stream
        index = min(self._result_index, len(self._results) - 1)
        self._result_index += 1
        return self._results[index]

    def reset(self, stream: FakeStream) -> None:
        del stream
        self.reset_calls += 1
        # A real reset clears the decoded text for the next turn.
        self._results = [""]
        self._result_index = 0


class FakeVad:
    """Duck-typed SileroVoiceActivityDetector with scripted probabilities."""

    def __init__(self, probabilities: list[float]) -> None:
        self._probabilities = list(probabilities)
        self.reset_calls = 0

    def process_frame(self, frame: Any) -> float:
        del frame
        if self._probabilities:
            return self._probabilities.pop(0)
        return 0.0

    def reset(self) -> None:
        self.reset_calls += 1


class FakeCompletionSession:
    """ONNX-session stand-in for smart-turn returning a fixed logit."""

    def __init__(self, logit: float) -> None:
        self._logit = logit
        self.run_calls = 0

    def run(self, output_names: object, feeds: dict[str, Any]) -> list[Any]:
        import numpy as np

        assert output_names is None
        assert feeds["input_features"].shape == (1, 80, 800)
        self.run_calls += 1
        return [np.array([[self._logit]], dtype=np.float32)]


@pytest.mark.anyio
async def test_sherpa_provider_streams_interims_then_semantic_final() -> None:
    pytest.importorskip("numpy")
    recognizer = FakeRecognizer(["", "HELLO", "HELLO WORLD", "HELLO WORLD", "HELLO WORLD"])
    vad = FakeVad([0.9, 0.9, 0.9, 0.1, 0.1])
    session = FakeCompletionSession(logit=4.0)
    provider = SherpaStreamingSpeechToText(
        model_dir="models/fake-zipformer",
        vad_model_path="models/fake-vad.onnx",
        semantic_check_silence_ms=64,
        endpoint_silence_ms=96,
        hard_silence_ms=160,
        flush_padding_ms=0,
        recognizer_factory=lambda: (recognizer, threading.Lock()),
        vad_factory=lambda: vad,
        semantic_endpointer=SmartTurnSemanticEndpointer(
            model_path="models/fake-smart-turn.onnx",
            threshold=0.5,
            session_factory=lambda: session,
        ),
    )

    await provider.start()
    for _ in range(3):  # 96ms of scripted speech
        await provider.accept_audio(_pcm_frame(1000))
    for _ in range(2):  # 64ms of scripted silence -> semantic gate fires
        await provider.accept_audio(_pcm_frame(0))

    events: list[TranscriptEvent] = []

    async def consume_until_final() -> None:
        async for event in provider.transcripts():
            events.append(event)
            if event.is_final:
                return

    await asyncio.wait_for(consume_until_final(), timeout=10.0)
    await provider.close()

    assert [(event.text, event.is_final) for event in events[:-1]] == [
        ("hello", False),
        ("hello world", False),
    ]
    final = events[-1]
    assert final.text == "hello world"
    assert final.is_final
    assert final.speech_final
    assert session.run_calls == 1  # smart-turn consulted exactly once
    assert recognizer.reset_calls == 1  # stream reset after the committed turn
    assert vad.reset_calls == 2  # once at pipeline start, once after the final

    async def drain_rest() -> list[TranscriptEvent]:
        return [event async for event in provider.transcripts()]

    assert await asyncio.wait_for(drain_rest(), timeout=10.0) == []


@pytest.mark.anyio
async def test_sherpa_provider_close_drains_queued_audio_before_ending() -> None:
    pytest.importorskip("numpy")
    recognizer = FakeRecognizer(["GOODBYE", "GOODBYE", "GOODBYE", "GOODBYE", "GOODBYE"])
    vad = FakeVad([0.9, 0.1, 0.1, 0.1, 0.1])
    provider = SherpaStreamingSpeechToText(
        model_dir="models/fake-zipformer",
        vad_model_path="models/fake-vad.onnx",
        semantic_check_silence_ms=32,
        endpoint_silence_ms=64,
        hard_silence_ms=96,
        flush_padding_ms=0,
        recognizer_factory=lambda: (recognizer, threading.Lock()),
        vad_factory=lambda: vad,
    )

    await provider.accept_audio(_pcm_frame(500))
    for _ in range(4):
        await provider.accept_audio(_pcm_frame(0))
    await provider.close()  # queued audio must still be decoded before the stream ends

    async def collect_all() -> list[TranscriptEvent]:
        return [event async for event in provider.transcripts()]

    events = await asyncio.wait_for(collect_all(), timeout=10.0)

    finals = [event for event in events if event.is_final]
    assert len(finals) == 1
    assert finals[0].text == "goodbye"
    assert finals[0].speech_final
