"""MlxWhisperSTT with a fake transcribe: VAD-gated buffering, end of turn, one final event."""

from __future__ import annotations

import asyncio
import builtins
import sys
from typing import Any

import numpy as np
import pytest

from voice_agent.providers.base import SpeechToText, TranscriptEvent
from voice_agent.providers.mlx_whisper_stt import (
    MIN_SPEECH_RMS,
    MlxWhisperSTT,
    MlxWhisperUnavailableError,
    mlx_transcriber,
)
from voice_agent.providers.smart_turn import SmartTurnSemanticEndpointer

FRAME = 640


class _FakeSession:
    """A smart-turn ONNX session that always answers `probability`."""

    def __init__(self, probability: float) -> None:
        self.probability = probability
        self.calls = 0

    def run(self, _outputs: Any, _inputs: Any) -> list[Any]:
        self.calls += 1
        return [np.array([[self.probability]], dtype=np.float32)]


async def _feed(stt: MlxWhisperSTT, pcm: bytes) -> None:
    for offset in range(0, len(pcm), FRAME):
        await stt.accept_audio(pcm[offset : offset + FRAME])


async def _finals(stt: MlxWhisperSTT, count: int = 1) -> list[TranscriptEvent]:
    events: list[TranscriptEvent] = []

    async def collect() -> None:
        async for event in stt.transcripts():
            events.append(event)
            if len(events) == count:
                return

    await asyncio.wait_for(collect(), timeout=5)
    return events


def _stt(transcribed: list[Any], energy_vad: Any, **kwargs: Any) -> MlxWhisperSTT:
    def transcribe(audio: Any) -> str:
        transcribed.append(audio)
        return "  I have chest pain   since this morning. "

    return MlxWhisperSTT(
        vad_model_path="unused", vad_factory=energy_vad, transcriber=transcribe, **kwargs
    )


def test_it_implements_the_speech_to_text_protocol(energy_vad: Any) -> None:
    stt: SpeechToText = _stt([], energy_vad)
    assert callable(stt.accept_audio) and callable(stt.transcripts) and callable(stt.close)


async def test_one_turn_is_buffered_transcribed_once_and_emitted_final(
    chest_pain_pcm: bytes, energy_vad: Any
) -> None:
    transcribed: list[Any] = []
    stt = _stt(transcribed, energy_vad)
    await stt.start()
    await _feed(stt, chest_pain_pcm)
    [event] = await _finals(stt)
    await stt.close()

    assert event == TranscriptEvent(
        text="I have chest pain since this morning.",
        is_final=True,
        speech_final=True,
        speech_ended_at=event.speech_ended_at,
    )
    assert event.speech_ended_at is not None
    [audio] = transcribed
    assert audio.dtype == np.float32 and float(np.max(np.abs(audio))) <= 1.0
    # The fixture is 0.5 s silence, ~1.9 s speech, 1.5 s silence: the turn holds the speech
    # plus a short pre-roll and the endpointing silence, not the whole file.
    total = len(chest_pain_pcm) // 2
    assert 1.5 * 16000 < len(audio) < total


async def test_silence_alone_never_reaches_the_transcriber(energy_vad: Any) -> None:
    transcribed: list[Any] = []
    stt = _stt(transcribed, energy_vad)
    await _feed(stt, bytes(16000 * 2 * 3))  # 3 s of silence
    await asyncio.sleep(0.2)
    await stt.close()
    assert transcribed == []


async def test_smart_turn_holds_an_unfinished_sentence_until_the_hard_cap(
    chest_pain_pcm: bytes, energy_vad: Any
) -> None:
    incomplete = _FakeSession(0.1)
    semantic = SmartTurnSemanticEndpointer(model_path="unused", session_factory=lambda: incomplete)
    transcribed: list[Any] = []
    stt = _stt(transcribed, energy_vad, semantic_endpointer=semantic, hard_silence_ms=1200)
    await _feed(stt, chest_pain_pcm)
    [event] = await _finals(stt)
    await stt.close()
    assert event.is_final and incomplete.calls >= 1
    # Committed only at the hard cap: the turn carries at least 1.2 s of trailing silence.
    tail = transcribed[0][-int(1.2 * 16000) :]
    assert float(np.max(np.abs(tail))) < 0.01


async def test_smart_turn_commits_a_finished_sentence_early(
    chest_pain_pcm: bytes, energy_vad: Any
) -> None:
    complete = _FakeSession(0.9)
    semantic = SmartTurnSemanticEndpointer(model_path="unused", session_factory=lambda: complete)
    transcribed: list[Any] = []
    stt = _stt(transcribed, energy_vad, semantic_endpointer=semantic, hard_silence_ms=1200)
    await _feed(stt, chest_pain_pcm)
    await _finals(stt)
    await stt.close()
    assert complete.calls == 1
    tail = transcribed[0][-int(0.6 * 16000) :]
    assert float(np.max(np.abs(tail))) > 0.01  # speech is less than 0.6 s from the end


async def test_two_utterances_are_two_turns(chest_pain_pcm: bytes, energy_vad: Any) -> None:
    transcribed: list[Any] = []
    stt = _stt(transcribed, energy_vad)
    await _feed(stt, chest_pain_pcm + chest_pain_pcm)
    events = await _finals(stt, count=2)
    await stt.close()
    assert [event.speech_final for event in events] == [True, True]
    assert len(transcribed) == 2


async def test_close_ends_the_transcript_stream(energy_vad: Any) -> None:
    stt = _stt([], energy_vad)
    await stt.start()
    await stt.close()
    assert [event async for event in stt.transcripts()] == []


async def test_start_raises_when_the_engines_cannot_load() -> None:
    def broken_vad() -> Any:
        raise RuntimeError("no VAD")

    stt = MlxWhisperSTT(vad_model_path="unused", vad_factory=broken_vad, transcriber=lambda _: "")
    with pytest.raises(MlxWhisperUnavailableError):
        await stt.start()
    await stt.close()


def test_mlx_transcriber_needs_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def no_mlx(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "mlx_whisper":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "mlx_whisper", raising=False)
    monkeypatch.setattr(builtins, "__import__", no_mlx)
    with pytest.raises(MlxWhisperUnavailableError, match="mlx-whisper"):
        mlx_transcriber("mlx-community/whisper-large-v3-turbo", "en")


def test_mlx_transcriber_refuses_a_missing_local_model(tmp_path: Any) -> None:
    with pytest.raises(MlxWhisperUnavailableError, match="download_models"):
        mlx_transcriber(str(tmp_path / "whisper-large-v3-turbo"), "en")


def test_mlx_transcriber_passes_the_model_and_language(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    class FakeMlxWhisper:
        @staticmethod
        def transcribe(audio: Any, **kwargs: Any) -> dict[str, Any]:
            calls.append({"samples": len(audio), **kwargs})
            return {"text": " chest pain "}

    monkeypatch.setitem(sys.modules, "mlx_whisper", FakeMlxWhisper)
    run = mlx_transcriber("mlx-community/whisper-large-v3-turbo", "en")
    assert run(np.zeros(1600, dtype=np.float32)) == " chest pain "
    assert calls == [
        {
            "samples": 1600,
            "path_or_hf_repo": "mlx-community/whisper-large-v3-turbo",
            "language": "en",
            "condition_on_previous_text": False,
            "verbose": None,
        }
    ]


class _EverythingIsSpeech:
    """A VAD that calls every frame speech, as a reset Silero can on faint noise."""

    def reset(self) -> None:
        return None

    def process_frame(self, frame: Any) -> float:
        return 1.0


async def test_mlx_whisper_near_silence_is_never_a_turn(chest_pain_pcm: bytes) -> None:
    """Whisper writes words into silence, so a turn of faint noise would be an answer the patient
    never gave: frames under -60 dBFS never count as speech (M2-02, ADR-058)."""
    transcribed: list[Any] = []
    stt = MlxWhisperSTT(
        vad_model_path="unused",
        vad_factory=_EverythingIsSpeech,
        transcriber=lambda audio: transcribed.append(audio) or "Thank you.",
    )
    await stt.start()
    rng = np.random.default_rng(0)
    faint = (rng.standard_normal(3 * 16000) * MIN_SPEECH_RMS * 0.3 * 32768).astype("<i2").tobytes()
    await _feed(stt, faint)  # 3 s of noise around -70 dBFS
    await stt.close()
    assert transcribed == []

    heard: list[Any] = []
    loud = MlxWhisperSTT(
        vad_model_path="unused",
        vad_factory=_EverythingIsSpeech,
        transcriber=lambda audio: heard.append(audio) or "I have chest pain.",
    )
    await loud.start()
    await _feed(loud, chest_pain_pcm)  # the answer itself still ends as one turn
    [event] = await _finals(loud)
    await loud.close()
    assert event.text == "I have chest pain." and len(heard) == 1
