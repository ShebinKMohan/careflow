import struct
import threading
from typing import Any

import pytest

import voice_agent.providers.kokoro_tts as kokoro_module
from voice_agent.providers.kokoro_tts import (
    KokoroTextToSpeech,
    KokoroTextToSpeechError,
    split_synthesis_units,
)
from voice_agent.telephony.plivo import PlivoAudioFormat


def test_split_synthesis_units_returns_empty_for_blank_input() -> None:
    assert split_synthesis_units("") == []
    assert split_synthesis_units("   \n\t ") == []


def test_split_synthesis_units_passes_single_short_sentence_through() -> None:
    assert split_synthesis_units("Hello there.") == ["Hello there."]


def test_split_synthesis_units_splits_on_sentence_boundaries() -> None:
    assert split_synthesis_units("First one. Second two! Third three?") == [
        "First one.",
        "Second two!",
        "Third three?",
    ]


def test_split_synthesis_units_splits_overlong_sentence_on_commas_then_words() -> None:
    assert split_synthesis_units("alpha beta, gamma delta, epsilon zeta", max_chars=20) == [
        "alpha beta,",
        "gamma delta,",
        "epsilon zeta",
    ]
    assert split_synthesis_units("one two three four five six", max_chars=10) == [
        "one two",
        "three",
        "four five",
        "six",
    ]


def test_split_synthesis_units_respects_max_chars() -> None:
    assert split_synthesis_units("abcdefghijkl", max_chars=5) == ["abcde", "fghij", "kl"]
    long_text = (
        "Verily, the quick brown fox jumps over the lazy dog while narrating "
        "an interminable soliloquy about nothing in particular"
    )
    units = split_synthesis_units(long_text, max_chars=24)
    assert units
    assert all(len(unit) <= 24 for unit in units)


def test_split_synthesis_units_caps_first_unit_at_word_boundary() -> None:
    assert split_synthesis_units(
        "Hello there my good friend and colleague. More text.",
        max_chars=200,
        first_unit_max_chars=20,
    ) == ["Hello there my good", "friend and colleague.", "More text."]


def test_split_synthesis_units_skips_split_for_marginal_overshoot() -> None:
    # A first sentence only slightly over the cap stays whole: the seam would buy
    # almost no latency, and a tiny stranded tail sounds worse than either.
    assert split_synthesis_units(
        "Hello there my good friend. More text.",
        max_chars=200,
        first_unit_max_chars=20,
    ) == ["Hello there my good friend.", "More text."]


def test_split_synthesis_units_prefers_comma_for_first_unit_split() -> None:
    assert split_synthesis_units(
        "Well now, let us continue onward. Done.",
        max_chars=200,
        first_unit_max_chars=20,
    ) == ["Well now,", "let us continue onward.", "Done."]


def test_split_synthesis_units_leaves_short_text_whole_despite_first_unit_cap() -> None:
    assert split_synthesis_units("Hi there.", max_chars=200, first_unit_max_chars=20) == [
        "Hi there."
    ]


def test_constructor_rejects_blank_and_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="model_path must not be blank"):
        KokoroTextToSpeech(model_path="   ", voices_path="v.bin")
    with pytest.raises(ValueError, match="voices_path must not be blank"):
        KokoroTextToSpeech(model_path="m.onnx", voices_path="   ")
    with pytest.raises(ValueError, match="voice must not be blank"):
        KokoroTextToSpeech(model_path="m.onnx", voices_path="v.bin", voice="   ")
    with pytest.raises(ValueError, match="lang must not be blank"):
        KokoroTextToSpeech(model_path="m.onnx", voices_path="v.bin", lang="   ")
    with pytest.raises(ValueError, match="speed must be positive"):
        KokoroTextToSpeech(model_path="m.onnx", voices_path="v.bin", speed=0.0)
    with pytest.raises(ValueError, match="max_unit_chars must be positive"):
        KokoroTextToSpeech(model_path="m.onnx", voices_path="v.bin", max_unit_chars=0)
    with pytest.raises(ValueError, match="first_unit_max_chars must be non-negative"):
        KokoroTextToSpeech(model_path="m.onnx", voices_path="v.bin", first_unit_max_chars=-1)


class FakeKokoroEngine:
    """Duck-typed kokoro_onnx.Kokoro yielding scripted 16kHz float32 waveforms."""

    def __init__(self, waveforms: list[Any] | None = None) -> None:
        self._waveforms = list(waveforms) if waveforms is not None else None
        self.calls: list[dict[str, Any]] = []

    def create(self, text: str, *, voice: str, speed: float, lang: str) -> tuple[Any, int]:
        import numpy as np

        self.calls.append({"text": text, "voice": voice, "speed": speed, "lang": lang})
        if self._waveforms is None:
            waveform: Any = np.full(4, 0.25, dtype=np.float32)
        elif self._waveforms:
            waveform = self._waveforms.pop(0)
        else:
            waveform = np.zeros(0, dtype=np.float32)
        # Native 16kHz so no resampling (soxr) is required in tests.
        return waveform, 16000


def _provider(engine: FakeKokoroEngine, **kwargs: Any) -> KokoroTextToSpeech:
    lock = threading.Lock()
    return KokoroTextToSpeech(
        model_path="models/kokoro-test.onnx",
        voices_path="models/voices-test.bin",
        engine_factory=lambda: (engine, lock),
        **kwargs,
    )


@pytest.mark.anyio
async def test_synthesize_yields_pcm16_per_unit_with_exact_conversion() -> None:
    np = pytest.importorskip("numpy")
    engine = FakeKokoroEngine(
        waveforms=[
            np.array([0.0, 0.5, -1.0, 2.0], dtype=np.float32),  # 2.0 must clip to 1.0
            np.array([0.25], dtype=np.float32),
        ]
    )
    provider = _provider(engine, first_unit_max_chars=0)  # sentence units only

    chunks = [
        chunk
        async for chunk in provider.synthesize(
            text="First one. Second two.", audio_format=PlivoAudioFormat.L16_16KHZ
        )
    ]

    assert len(chunks) == 2
    assert chunks[0] == struct.pack("<4h", 0, 16383, -32767, 32767)
    assert chunks[1] == struct.pack("<h", 8191)
    assert [call["text"] for call in engine.calls] == ["First one.", "Second two."]


@pytest.mark.anyio
async def test_synthesize_caps_first_unit_for_fast_first_audio_by_default() -> None:
    pytest.importorskip("numpy")
    engine = FakeKokoroEngine()
    provider = _provider(engine)  # default first_unit_max_chars=20

    chunks = [
        chunk
        async for chunk in provider.synthesize(
            text="Hello there my good friend and colleague. More text.",
            audio_format=PlivoAudioFormat.L16_16KHZ,
        )
    ]

    assert len(chunks) == 3
    assert [call["text"] for call in engine.calls] == [
        "Hello there my good",
        "friend and colleague.",
        "More text.",
    ]


@pytest.mark.anyio
async def test_synthesize_rejects_non_l16_16khz_format() -> None:
    provider = _provider(FakeKokoroEngine())

    with pytest.raises(ValueError, match="PCM16 16kHz"):
        async for _ in provider.synthesize(
            text="Hi.", audio_format=PlivoAudioFormat.MULAW_8KHZ
        ):
            pass


@pytest.mark.anyio
async def test_synthesize_rejects_blank_text() -> None:
    provider = _provider(FakeKokoroEngine())

    with pytest.raises(KokoroTextToSpeechError, match="must not be blank"):
        async for _ in provider.synthesize(
            text="   ", audio_format=PlivoAudioFormat.L16_16KHZ
        ):
            pass


@pytest.mark.anyio
async def test_synthesize_raises_when_engine_returns_no_audio() -> None:
    np = pytest.importorskip("numpy")
    engine = FakeKokoroEngine(
        waveforms=[np.zeros(0, dtype=np.float32), np.zeros(0, dtype=np.float32)]
    )
    provider = _provider(engine)

    with pytest.raises(KokoroTextToSpeechError, match="returned no audio"):
        async for _ in provider.synthesize(
            text="One. Two.", audio_format=PlivoAudioFormat.L16_16KHZ
        ):
            pass


@pytest.mark.anyio
async def test_start_warms_shared_engine_once_per_model_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("numpy")
    monkeypatch.setattr(kokoro_module, "_WARMED_ENGINES", set())
    engine = FakeKokoroEngine()
    lock = threading.Lock()
    first = KokoroTextToSpeech(
        model_path="models/kokoro-test.onnx",
        voices_path="models/voices-test.bin",
        engine_factory=lambda: (engine, lock),
    )
    second = KokoroTextToSpeech(
        model_path="models/kokoro-test.onnx",
        voices_path="models/voices-test.bin",
        engine_factory=lambda: (engine, lock),
    )

    await first.start()
    await second.start()

    assert [call["text"] for call in engine.calls] == ["Hi."]


@pytest.mark.anyio
async def test_voice_speed_and_lang_are_passed_through_to_engine() -> None:
    pytest.importorskip("numpy")
    engine = FakeKokoroEngine()
    provider = _provider(engine, voice="am_adam", speed=1.3, lang="en-gb")

    chunks = [
        chunk
        async for chunk in provider.synthesize(
            text="Hello there.", audio_format=PlivoAudioFormat.L16_16KHZ
        )
    ]

    assert chunks
    call = engine.calls[0]
    assert call["voice"] == "am_adam"
    assert call["speed"] == 1.3
    assert call["lang"] == "en-gb"
