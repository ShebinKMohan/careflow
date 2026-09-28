"""MMS-TTS Arabic (M2-02, the Arabic voice since ADR-033 replaced SILMA) with a fake VITS engine:
sentence units at Arabic punctuation, resampling to the channel's rate, barge-in and warm-up, and
clear errors when the model or the extra is missing. No real model loads here."""

from __future__ import annotations

import asyncio
import sys
import threading
import types
from typing import Any

import numpy as np
import pytest

from voice_agent.providers import mms_tts
from voice_agent.providers.base import TextToSpeech
from voice_agent.providers.mms_tts import MmsTextToSpeech, MmsTextToSpeechError, split_units
from voice_agent.telephony.plivo import PlivoAudioFormat

NATIVE_RATE = 16000


class _NoGrad:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> bool:
        return False


@pytest.fixture(autouse=True)
def fake_torch(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_render` needs only `torch.no_grad`; CI does not install torch (the arabic-speech extra)."""
    torch = types.ModuleType("torch")
    torch.no_grad = _NoGrad  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "torch", torch)


class _Tensor:
    def __init__(self, array: np.ndarray[Any, Any]) -> None:
        self.array = array

    def cpu(self) -> _Tensor:
        return self

    def numpy(self) -> np.ndarray[Any, Any]:
        return self.array


class _Tokenizer:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def __call__(self, text: str, return_tensors: str) -> dict[str, Any]:
        assert return_tensors == "pt"
        self.texts.append(text)
        return {"input_ids": np.ones((1, len(text)), dtype=np.int64)}


class _Vits:
    """A VITS model: 0.1 s of a 16 kHz tone per call; `hold` blocks the given call number."""

    def __init__(self, hold: int | None = None) -> None:
        self.config = types.SimpleNamespace(sampling_rate=NATIVE_RATE)
        self.calls = 0
        self.hold = hold
        self.holding = threading.Event()
        self.release = threading.Event()

    def __call__(self, **inputs: Any) -> Any:
        self.calls += 1
        if self.calls == self.hold:
            self.holding.set()
            assert self.release.wait(5)
        samples = np.arange(NATIVE_RATE // 10)
        tone = 0.2 * np.sin(2 * np.pi * 220 * samples / NATIVE_RATE)
        return types.SimpleNamespace(waveform=[_Tensor(tone.astype(np.float32))])


def _tts(
    model: _Vits, tokenizer: _Tokenizer, lock: threading.Lock | None = None, key: str = "unused"
) -> MmsTextToSpeech:
    engine = (model, tokenizer, lock or threading.Lock())
    return MmsTextToSpeech(model_path=key, engine_factory=lambda: engine)


async def _speak(tts: MmsTextToSpeech, text: str, audio_format: PlivoAudioFormat) -> list[bytes]:
    return [chunk async for chunk in tts.synthesize(text=text, audio_format=audio_format)]


def test_mms_implements_the_text_to_speech_protocol() -> None:
    tts: TextToSpeech = _tts(_Vits(), _Tokenizer())
    assert callable(tts.synthesize) and callable(tts.close)


def test_mms_splits_at_arabic_sentence_ends() -> None:
    text = "عندي ألم في صدري.  من متى بدأ؟ من ساعة؛ تقريبا"
    assert split_units(text) == ["عندي ألم في صدري.", "من متى بدأ؟", "من ساعة؛", "تقريبا"]


def test_mms_cuts_an_over_long_sentence_at_a_word() -> None:
    sentence = " ".join(["كلمة"] * 12)  # 59 characters, no sentence end
    units = split_units(sentence, max_chars=20)
    assert all(len(unit) <= 20 for unit in units)
    assert " ".join(units) == sentence  # nothing lost, no word split


@pytest.mark.parametrize(
    ("audio_format", "samples"),
    [(PlivoAudioFormat.L16_24KHZ, 2400), (PlivoAudioFormat.L16_16KHZ, 1600)],
)
async def test_mms_yields_one_pcm16_chunk_per_sentence_at_the_channels_rate(
    audio_format: PlivoAudioFormat, samples: int
) -> None:
    model, tokenizer = _Vits(), _Tokenizer()
    chunks = await _speak(_tts(model, tokenizer), "أهلا. كيف حالك؟", audio_format)
    assert tokenizer.texts == ["أهلا.", "كيف حالك؟"]
    assert [len(chunk) for chunk in chunks] == [samples * 2, samples * 2]  # 0.1 s each, PCM16
    pcm = np.frombuffer(chunks[0], dtype="<i2")
    assert 0 < np.abs(pcm).max() < 32767  # the tone, scaled, not clipped


async def test_mms_refuses_a_format_it_cannot_serve_and_blank_text() -> None:
    tts = _tts(_Vits(), _Tokenizer())
    with pytest.raises(ValueError, match="PCM16"):
        await _speak(tts, "أهلا.", PlivoAudioFormat.MULAW_8KHZ)
    with pytest.raises(MmsTextToSpeechError, match="blank"):
        await _speak(tts, "   ", PlivoAudioFormat.L16_24KHZ)


async def test_mms_a_barge_in_renders_no_further_sentence() -> None:
    model, tokenizer = _Vits(hold=2), _Tokenizer()
    tts = _tts(model, tokenizer)
    chunks: list[bytes] = []

    async def speak() -> None:
        async for chunk in tts.synthesize(
            text="الأولى. الثانية. الثالثة.", audio_format=PlivoAudioFormat.L16_24KHZ
        ):
            chunks.append(chunk)

    task = asyncio.create_task(speak())
    assert await asyncio.to_thread(model.holding.wait, 5)  # the second sentence is rendering
    task.cancel()  # the patient speaks over the question
    model.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.05)
    assert len(chunks) == 1
    assert tokenizer.texts == ["الأولى.", "الثانية."]  # the third is never rendered


async def test_mms_a_sentence_queued_behind_another_session_is_dropped_after_a_barge_in() -> None:
    model, tokenizer = _Vits(), _Tokenizer()
    engine_lock = threading.Lock()
    tts = _tts(model, tokenizer, engine_lock)
    engine_lock.acquire()  # another session is synthesizing on the shared engine
    task = asyncio.create_task(_speak(tts, "الأولى.", PlivoAudioFormat.L16_24KHZ))
    await asyncio.sleep(0.1)  # the unit waits for the engine
    task.cancel()  # the patient speaks over the question
    with pytest.raises(asyncio.CancelledError):
        await task
    engine_lock.release()  # the other session finishes; the queued unit gets the engine
    await asyncio.sleep(0.1)
    assert tokenizer.texts == [] and model.calls == 0  # it returned without rendering


async def test_mms_warms_once_per_process() -> None:
    tokenizer = _Tokenizer()
    key = "warm-once-test"
    mms_tts._WARMED.discard(key)
    for _ in range(2):
        await _tts(_Vits(), tokenizer, key=key).start()
    assert tokenizer.texts == ["مرحبا."]
    mms_tts._WARMED.discard(key)


async def test_mms_a_missing_model_directory_names_the_download_script(tmp_path: Any) -> None:
    tts = MmsTextToSpeech(model_path=str(tmp_path / "mms-tts-ara"))
    with pytest.raises(MmsTextToSpeechError, match="download_models.sh"):
        await tts.start()


def test_mms_without_the_arabic_speech_extra_names_it(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "transformers", None)  # `import transformers` fails
    with pytest.raises(MmsTextToSpeechError, match="arabic-speech"):
        mms_tts._load(str(tmp_path))


def test_mms_loads_only_from_the_local_directory(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    loads: list[tuple[str, str, dict[str, Any]]] = []
    module = types.ModuleType("transformers")

    class _Loaded:
        def eval(self) -> _Loaded:
            return self

    class AutoTokenizer:
        @staticmethod
        def from_pretrained(path: str, **kwargs: Any) -> object:
            loads.append(("tokenizer", path, kwargs))
            return object()

    class VitsModel:
        @staticmethod
        def from_pretrained(path: str, **kwargs: Any) -> _Loaded:
            loads.append(("model", path, kwargs))
            return _Loaded()

    module.AutoTokenizer = AutoTokenizer  # type: ignore[attr-defined]
    module.VitsModel = VitsModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "transformers", module)
    mms_tts._load(str(tmp_path))
    assert loads == [
        ("tokenizer", str(tmp_path), {"local_files_only": True}),
        ("model", str(tmp_path), {"local_files_only": True}),
    ]
