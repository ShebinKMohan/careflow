"""Cohere Transcribe Arabic (M2-02) with fakes: the gated model is never downloaded, the model
card's inference runs in Arabic, and the turn logic is `MlxWhisperSTT`'s. No real model loads
here; the gated model itself is `not_run` until the owner unlocks it (ADR-033)."""

from __future__ import annotations

import asyncio
import sys
import types
from typing import Any

import httpx
import numpy as np
import pytest
from huggingface_hub.errors import GatedRepoError, HfHubHTTPError, LocalEntryNotFoundError

from voice_agent.providers import cohere_transcribe_stt as cohere
from voice_agent.providers.base import SpeechToText, TranscriptEvent
from voice_agent.providers.cohere_transcribe_stt import (
    GATED_REASON,
    MODEL_ID,
    MODEL_REVISION,
    CohereTranscribeSTT,
    CohereTranscribeUnavailableError,
    cohere_stt,
    cohere_transcriber,
    ensure_accessible,
)
from voice_agent.providers.mlx_whisper_stt import SAMPLE_RATE, MlxWhisperSTT

FRAME = 640
SPOKEN = "عندي ألم في صدري من ساعة"


def _http_error(cls: type[HfHubHTTPError], status: int) -> HfHubHTTPError:
    request = httpx.Request("GET", f"https://huggingface.co/api/models/{MODEL_ID}")
    return cls(f"{status} from the Hub", response=httpx.Response(status, request=request))


class _Hub:
    """huggingface_hub's two calls: the local cache lookup and `model_info`."""

    def __init__(self, *, cached: bool, info: Any = None, error: Exception | None = None) -> None:
        self.cached = cached
        self.info = info
        self.error = error
        self.downloads: list[dict[str, Any]] = []
        self.infos: list[tuple[str, str | None]] = []

    def hf_hub_download(self, repo_id: str, filename: str, **kwargs: Any) -> str:
        self.downloads.append({"repo_id": repo_id, "filename": filename, **kwargs})
        if not self.cached:
            raise LocalEntryNotFoundError("not in the local cache")
        return f"/cache/{repo_id}/{filename}"

    def model_info(self, repo_id: str, revision: str | None = None) -> Any:
        self.infos.append((repo_id, revision))
        if self.error is not None:
            raise self.error
        return self.info


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> Any:
    def install(**kwargs: Any) -> _Hub:
        fake = _Hub(**kwargs)
        import huggingface_hub

        monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake.hf_hub_download)
        monkeypatch.setattr(huggingface_hub, "model_info", fake.model_info)
        return fake

    return install


def test_cohere_a_cached_pinned_snapshot_is_accessible_without_the_network(hub: Any) -> None:
    fake = hub(cached=True)
    ensure_accessible()
    [download] = fake.downloads
    assert download == {
        "repo_id": MODEL_ID,
        "filename": "config.json",
        "revision": MODEL_REVISION,
        "local_files_only": True,  # never a download
    }
    assert fake.infos == []  # no Hub call at all


@pytest.mark.parametrize(
    ("info", "error"),
    [
        (types.SimpleNamespace(gated="auto"), None),
        (None, _http_error(GatedRepoError, 403)),
        (None, _http_error(HfHubHTTPError, 401)),
        (None, _http_error(HfHubHTTPError, 403)),
    ],
    ids=["gated-auto", "gated-repo-error", "401", "403"],
)
def test_cohere_a_gated_model_is_refused_with_the_owners_step(
    hub: Any, info: Any, error: Exception | None
) -> None:
    fake = hub(cached=False, info=info, error=error)
    with pytest.raises(CohereTranscribeUnavailableError, match=GATED_REASON):
        ensure_accessible()
    assert all(call["local_files_only"] is True for call in fake.downloads)
    assert fake.infos == [(MODEL_ID, MODEL_REVISION)]


@pytest.mark.parametrize(
    ("info", "error", "reason"),
    [
        (types.SimpleNamespace(gated=False), None, "not downloaded"),
        (None, _http_error(HfHubHTTPError, 500), "HTTP 500 from Hugging Face"),
        (None, httpx.ConnectError("offline"), "ConnectError from Hugging Face"),
    ],
    ids=["ungated", "server-error", "offline"],
)
def test_cohere_a_model_missing_for_another_reason_says_so(
    hub: Any, info: Any, error: Exception | None, reason: str
) -> None:
    hub(cached=False, info=info, error=error)
    with pytest.raises(CohereTranscribeUnavailableError, match=reason) as raised:
        ensure_accessible()
    assert GATED_REASON not in str(raised.value)


# --- the model card's inference, with fake torch and transformers modules ---


class _NoGrad:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> bool:
        return False


def _fake_torch(*, mps: bool) -> types.ModuleType:
    torch = types.ModuleType("torch")
    torch.no_grad = _NoGrad  # type: ignore[attr-defined]
    torch.backends = types.SimpleNamespace(  # type: ignore[attr-defined]
        mps=types.SimpleNamespace(is_available=lambda: mps)
    )
    return torch


class _Inputs(dict[str, Any]):
    def __init__(self, audio: Any) -> None:
        super().__init__(input_features=audio)
        self.moved: list[tuple[Any, Any]] = []

    def to(self, device: Any, dtype: Any = None) -> _Inputs:
        self.moved.append((device, dtype))
        return self


class _Processor:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.inputs: list[_Inputs] = []
        self.decoded: list[tuple[Any, bool]] = []

    def __call__(self, audio: Any, **kwargs: Any) -> _Inputs:
        self.calls.append({"samples": len(audio), **kwargs})
        inputs = _Inputs(audio)
        self.inputs.append(inputs)
        return inputs

    def decode(self, tokens: Any, skip_special_tokens: bool = False) -> str:
        self.decoded.append((tokens, skip_special_tokens))
        return SPOKEN


class _Model:
    device = "mps"
    dtype = "bfloat16"

    def __init__(self) -> None:
        self.generated: list[dict[str, Any]] = []
        self.moved_to: list[str] = []
        self.evaluated = False

    def generate(self, **kwargs: Any) -> list[list[int]]:
        self.generated.append(kwargs)
        return [[7, 8, 9]]

    def to(self, device: str) -> _Model:
        self.moved_to.append(device)
        return self

    def eval(self) -> _Model:
        self.evaluated = True
        return self


def test_cohere_transcriber_runs_the_model_card_inference_in_arabic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processor, model = _Processor(), _Model()
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(mps=True))
    loads: list[tuple[str, str]] = []

    def engine(model_id: str, revision: str) -> tuple[Any, Any]:
        loads.append((model_id, revision))
        return processor, model

    monkeypatch.setattr(cohere, "_cached_engine", engine)
    transcribe = cohere_transcriber()
    audio = np.zeros(SAMPLE_RATE, dtype=np.float32)

    assert transcribe(audio) == SPOKEN
    assert loads == [(MODEL_ID, MODEL_REVISION)]
    [call] = processor.calls
    assert call == {
        "samples": SAMPLE_RATE,
        "sampling_rate": 16000,
        "return_tensors": "pt",
        "language": "ar",  # the session's language, never detected
    }
    assert processor.inputs[0].moved == [("mps", "bfloat16")]
    [generated] = model.generated
    assert generated["max_new_tokens"] == 256
    assert processor.decoded == [([7, 8, 9], True)]


def _fake_transformers(processor: _Processor, model: _Model, loads: list[Any]) -> types.ModuleType:
    module = types.ModuleType("transformers")

    class AutoProcessor:
        @staticmethod
        def from_pretrained(model_id: str, **kwargs: Any) -> _Processor:
            loads.append(("processor", model_id, kwargs))
            return processor

    class CohereAsrForConditionalGeneration:
        @staticmethod
        def from_pretrained(model_id: str, **kwargs: Any) -> _Model:
            loads.append(("model", model_id, kwargs))
            return model

    module.AutoProcessor = AutoProcessor  # type: ignore[attr-defined]
    module.CohereAsrForConditionalGeneration = CohereAsrForConditionalGeneration  # type: ignore[attr-defined]
    return module


@pytest.mark.parametrize(("mps", "device"), [(True, "mps"), (False, "cpu")])
def test_cohere_loads_the_pinned_revision_locally_on_mps_else_cpu(
    monkeypatch: pytest.MonkeyPatch, hub: Any, mps: bool, device: str
) -> None:
    hub(cached=True)
    processor, model = _Processor(), _Model()
    loads: list[Any] = []
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(mps=mps))
    monkeypatch.setitem(sys.modules, "transformers", _fake_transformers(processor, model, loads))

    assert cohere._load(MODEL_ID, MODEL_REVISION) == (processor, model)
    pinned = {"revision": MODEL_REVISION, "local_files_only": True}
    assert loads == [
        ("processor", MODEL_ID, pinned),
        ("model", MODEL_ID, {**pinned, "dtype": "auto"}),  # no trust_remote_code
    ]
    assert model.moved_to == [device] and model.evaluated


def test_cohere_without_the_arabic_speech_extra_names_it(
    monkeypatch: pytest.MonkeyPatch, hub: Any
) -> None:
    hub(cached=True)
    monkeypatch.setitem(sys.modules, "transformers", None)  # `import transformers` fails
    with pytest.raises(CohereTranscribeUnavailableError, match="arabic-speech"):
        cohere._load(MODEL_ID, MODEL_REVISION)


def test_cohere_a_gated_model_never_reaches_transformers(
    monkeypatch: pytest.MonkeyPatch, hub: Any
) -> None:
    hub(cached=False, info=types.SimpleNamespace(gated="auto"))
    loads: list[Any] = []
    monkeypatch.setitem(
        sys.modules, "transformers", _fake_transformers(_Processor(), _Model(), loads)
    )
    with pytest.raises(CohereTranscribeUnavailableError, match=GATED_REASON):
        cohere._load(MODEL_ID, MODEL_REVISION)
    assert loads == []


# --- the turn: MlxWhisperSTT's logic, one final Arabic transcript ---


async def _feed(stt: MlxWhisperSTT, pcm: bytes) -> None:
    for offset in range(0, len(pcm), FRAME):
        await stt.accept_audio(pcm[offset : offset + FRAME])


def test_cohere_stt_is_its_own_arabic_class_on_the_shared_turn_logic() -> None:
    stt: SpeechToText = cohere_stt(vad_model_path="unused", transcriber=lambda _: SPOKEN)
    assert isinstance(stt, CohereTranscribeSTT) and isinstance(stt, MlxWhisperSTT)
    assert type(MlxWhisperSTT(vad_model_path="unused")) is not CohereTranscribeSTT
    assert (stt._model, stt._language) == (MODEL_ID, "ar")


async def test_cohere_stt_emits_one_final_arabic_transcript_per_turn(
    chest_pain_pcm: bytes, energy_vad: Any
) -> None:
    heard: list[int] = []

    def transcribe(audio: Any) -> str:
        heard.append(len(audio))
        return f"  {SPOKEN}  " if len(audio) > SAMPLE_RATE // 2 else ""  # the warm-up is silence

    stt = CohereTranscribeSTT(
        vad_model_path="unused", vad_factory=energy_vad, transcriber=transcribe
    )
    await stt.start()
    await _feed(stt, chest_pain_pcm)
    event = await asyncio.wait_for(anext(aiter(stt.transcripts())), timeout=5)
    await stt.close()

    assert event.text == SPOKEN
    assert (event.is_final, event.speech_final) == (True, True)
    assert isinstance(event, TranscriptEvent) and event.speech_ended_at is not None
    assert len([n for n in heard if n > SAMPLE_RATE // 2]) == 1  # transcribed once per turn
