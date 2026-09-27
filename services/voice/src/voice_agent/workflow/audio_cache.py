from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Literal, Protocol

AudioCachePurpose = Literal[
    "pre_response_ack",
    "greeting",
    "fallback",
    "fast_path",
]


@dataclass(frozen=True, slots=True)
class AudioCacheContext:
    tts_provider: str
    voice: str | None = None
    model: str | None = None
    language: str | None = None
    pace: float | None = None

    def __post_init__(self) -> None:
        if not self.tts_provider.strip():
            raise ValueError("tts_provider must not be blank.")

    def key(
        self,
        *,
        purpose: AudioCachePurpose,
        text: str,
        audio_format: str,
        sample_rate: int,
    ) -> AudioCacheKey:
        return AudioCacheKey(
            purpose=purpose,
            text=_normalize_text(text),
            audio_format=audio_format,
            sample_rate=sample_rate,
            tts_provider=self.tts_provider.strip().lower(),
            voice=_normalize_optional(self.voice),
            model=_normalize_optional(self.model),
            language=_normalize_optional(self.language),
            pace=self.pace,
        )


@dataclass(frozen=True, slots=True)
class AudioCacheKey:
    purpose: AudioCachePurpose
    text: str
    audio_format: str
    sample_rate: int
    tts_provider: str
    voice: str | None
    model: str | None
    language: str | None
    pace: float | None

    def __post_init__(self) -> None:
        if not self.text:
            raise ValueError("text must not be blank.")
        if not self.audio_format.strip():
            raise ValueError("audio_format must not be blank.")
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive.")
        if not self.tts_provider.strip():
            raise ValueError("tts_provider must not be blank.")


class AudioCache(Protocol):
    def get(self, key: AudioCacheKey) -> bytes | None: ...

    def set(self, key: AudioCacheKey, audio: bytes) -> None: ...


class InMemoryAudioCache:
    def __init__(self, *, max_entries: int = 256, max_bytes: int = 8 * 1024 * 1024) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive.")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive.")
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._items: OrderedDict[AudioCacheKey, bytes] = OrderedDict()
        self._total_bytes = 0
        self.hit_count = 0
        self.miss_count = 0
        self.set_count = 0

    def get(self, key: AudioCacheKey) -> bytes | None:
        audio = self._items.get(key)
        if audio is None:
            self.miss_count += 1
            return None
        self.hit_count += 1
        self._items.move_to_end(key)
        return audio

    def set(self, key: AudioCacheKey, audio: bytes) -> None:
        if not audio:
            return
        if len(audio) > self._max_bytes:
            self._remove(key)
            return
        self._remove(key)
        self._items[key] = audio
        self._total_bytes += len(audio)
        self.set_count += 1
        self._evict_until_bounded()

    @property
    def size(self) -> int:
        return len(self._items)

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    def _remove(self, key: AudioCacheKey) -> None:
        old_audio = self._items.pop(key, None)
        if old_audio is not None:
            self._total_bytes -= len(old_audio)

    def _evict_until_bounded(self) -> None:
        while len(self._items) > self._max_entries or self._total_bytes > self._max_bytes:
            _key, audio = self._items.popitem(last=False)
            self._total_bytes -= len(audio)


def _normalize_text(value: str) -> str:
    clean_value = " ".join(value.strip().split())
    if not clean_value:
        raise ValueError("text must not be blank.")
    return clean_value


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    clean_value = value.strip().lower()
    return clean_value or None
