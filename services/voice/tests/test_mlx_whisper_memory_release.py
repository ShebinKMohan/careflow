"""Whisper memory handoff and cross-language serialization without loading any models."""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from voice_agent.providers import mlx_whisper_stt as stt


class FakeEngines:
    def __init__(self) -> None:
        self.holder = SimpleNamespace(model=None, model_path=None)
        self.events: list[str] = []
        self.calls: list[dict[str, Any]] = []
        self.transcription_error: Exception | None = None
        self.cleanup_error: Exception | None = None
        self.on_decode: Callable[[str], None] = lambda _language: None
        self.on_synchronize: Callable[[], None] = lambda: None

    def transcribe(self, _audio: Any, **kwargs: Any) -> dict[str, str]:
        # A following language must reload after the preceding turn releases both fields.
        assert self.holder.model is None
        assert self.holder.model_path is None
        self.holder.model = object()
        self.holder.model_path = kwargs["path_or_hf_repo"]
        self.calls.append(kwargs)
        language = kwargs["language"]
        self.events.append(f"decode:{language}")
        self.on_decode(language)
        if self.transcription_error is not None:
            raise self.transcription_error
        return {"text": f"transcript:{language}"}

    def synchronize(self) -> None:
        self.events.append("synchronize")
        assert self.holder.model is not None
        assert self.holder.model_path is not None
        self.on_synchronize()
        if self.cleanup_error is not None:
            raise self.cleanup_error

    def collect(self) -> int:
        self.events.append("collect")
        assert self.holder.model is None
        assert self.holder.model_path is None
        return 0

    def clear_cache(self) -> None:
        self.events.append("clear_cache")
        assert self.holder.model is None
        assert self.holder.model_path is None


@pytest.fixture
def engines(monkeypatch: pytest.MonkeyPatch) -> FakeEngines:
    fake = FakeEngines()
    core = SimpleNamespace(
        synchronize=fake.synchronize,
        get_active_memory=lambda: 10 if fake.holder.model is not None else 0,
        get_cache_memory=lambda: 0,
        clear_cache=fake.clear_cache,
    )
    # Replace every imported MLX module so neither installed libraries nor models are used.
    monkeypatch.setitem(sys.modules, "mlx_whisper", SimpleNamespace(transcribe=fake.transcribe))
    monkeypatch.setitem(
        sys.modules, "mlx_whisper.transcribe", SimpleNamespace(ModelHolder=fake.holder)
    )
    monkeypatch.setitem(sys.modules, "mlx", SimpleNamespace(core=core))
    monkeypatch.setitem(sys.modules, "mlx.core", core)
    monkeypatch.setattr(stt, "gc", SimpleNamespace(collect=fake.collect))
    return fake


def test_success_releases_weights_before_returning_the_transcript(engines: FakeEngines) -> None:
    run = stt.mlx_transcriber("fake-whisper-model", "en")

    assert run(np.zeros(16, dtype=np.float32)) == "transcript:en"

    assert engines.events == ["decode:en", "synchronize", "collect", "clear_cache"]
    assert engines.holder.model is None
    assert engines.holder.model_path is None


def test_transcription_failure_still_releases_weights_and_preserves_error(
    engines: FakeEngines,
) -> None:
    original = RuntimeError("decoder failed")
    engines.transcription_error = original
    run = stt.mlx_transcriber("fake-whisper-model", "en")

    with pytest.raises(RuntimeError) as caught:
        run(np.zeros(16, dtype=np.float32))

    assert caught.value is original
    assert engines.events == ["decode:en", "synchronize", "collect", "clear_cache"]
    assert engines.holder.model is None
    assert engines.holder.model_path is None


@pytest.mark.parametrize("decode_fails", [False, True])
def test_cleanup_failure_preserves_transcript_or_original_transcription_error(
    engines: FakeEngines, caplog: pytest.LogCaptureFixture, decode_fails: bool
) -> None:
    original = ValueError("original decoder failure")
    engines.transcription_error = original if decode_fails else None
    engines.cleanup_error = RuntimeError("fake synchronization failure")
    run = stt.mlx_transcriber("fake-whisper-model", "en")

    if decode_fails:
        with pytest.raises(ValueError) as caught:
            run(np.zeros(16, dtype=np.float32))
        assert caught.value is original
    else:
        assert run(np.zeros(16, dtype=np.float32)) == "transcript:en"

    assert engines.events == ["decode:en", "synchronize"]
    assert "Whisper memory release failed after transcription" in caplog.text


def test_sequential_languages_reload_and_release_the_same_model(engines: FakeEngines) -> None:
    english = stt.mlx_transcriber("fake-whisper-model", "en")
    arabic = stt.mlx_transcriber("fake-whisper-model", "ar")
    audio = np.zeros(16, dtype=np.float32)

    assert english(audio) == "transcript:en"
    assert arabic(audio) == "transcript:ar"

    assert engines.events == [
        "decode:en",
        "synchronize",
        "collect",
        "clear_cache",
        "decode:ar",
        "synchronize",
        "collect",
        "clear_cache",
    ]
    assert engines.calls == [
        {
            "path_or_hf_repo": "fake-whisper-model",
            "language": language,
            "condition_on_previous_text": False,
            "verbose": None,
        }
        for language in ("en", "ar")
    ]
    assert engines.holder.model is None
    assert engines.holder.model_path is None


def test_other_language_waits_for_both_decoding_and_memory_release(
    engines: FakeEngines, monkeypatch: pytest.MonkeyPatch
) -> None:
    decode_started = threading.Event()
    allow_decode = threading.Event()
    cleanup_started = threading.Event()
    allow_cleanup = threading.Event()
    second_attempted_lock = threading.Event()
    arabic_started = threading.Event()
    real_lock = stt._TRANSCRIBE_LOCK

    class ObservedLock:
        """Observe contention on the production lock without relying on sleep timing."""

        def __enter__(self) -> None:
            if decode_started.is_set():
                second_attempted_lock.set()
            real_lock.acquire()

        def __exit__(self, *_exc: object) -> None:
            real_lock.release()

    def decode(language: str) -> None:
        if language == "en":
            decode_started.set()
            assert allow_decode.wait(5), "test did not release English decoding"
        else:
            arabic_started.set()

    def synchronize() -> None:
        if not arabic_started.is_set():
            cleanup_started.set()
            assert allow_cleanup.wait(5), "test did not release English cleanup"

    monkeypatch.setattr(stt, "_TRANSCRIBE_LOCK", ObservedLock())
    engines.on_decode = decode
    engines.on_synchronize = synchronize
    english = stt.mlx_transcriber("fake-whisper-model", "en")
    arabic = stt.mlx_transcriber("fake-whisper-model", "ar")
    audio = np.zeros(16, dtype=np.float32)

    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(english, audio)
        try:
            assert decode_started.wait(5)
            second = workers.submit(arabic, audio)
            assert second_attempted_lock.wait(5)
            assert not arabic_started.is_set()
            allow_decode.set()
            assert cleanup_started.wait(5)
            assert not arabic_started.is_set()
            assert not first.done()
        finally:
            # Never leave worker threads blocked if an assertion fails.
            allow_decode.set()
            allow_cleanup.set()

        assert first.result(timeout=5) == "transcript:en"
        assert second.result(timeout=5) == "transcript:ar"

    assert engines.events == [
        "decode:en",
        "synchronize",
        "collect",
        "clear_cache",
        "decode:ar",
        "synchronize",
        "collect",
        "clear_cache",
    ]
