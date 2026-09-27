from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from voice_agent.telephony.plivo import PlivoAudioFormat


@dataclass(frozen=True, slots=True)
class TranscriptEvent:
    text: str
    is_final: bool
    confidence: float | None = None
    # `is_final` means this text segment is finalized and will not change.
    # `speech_final` means the caller's whole utterance has ended (end-of-turn).
    # Deepgram emits several is_final segments before one speech_final; collapsing
    # them is the classic "agent talks over the caller" bug.
    speech_final: bool = False
    # Monotonic time when the caller's speech ended, if the engine knows it (turn latency).
    speech_ended_at: float | None = None


class SpeechToText(Protocol):
    async def accept_audio(self, audio: bytes) -> TranscriptEvent | None: ...

    def transcripts(self) -> AsyncIterator[TranscriptEvent]: ...

    async def close(self) -> None: ...


class LLMClient(Protocol):
    def generate_reply(self, *, call_id: str, user_text: str) -> AsyncIterator[str]: ...

    async def close(self) -> None: ...


@runtime_checkable
class DeferredHistoryLLMClient(Protocol):
    def generate_reply(
        self,
        *,
        call_id: str,
        user_text: str,
        commit_history: bool = True,
    ) -> AsyncIterator[str]: ...

    def remember_turn(self, *, call_id: str, user_text: str, assistant_text: str) -> None: ...


class TextToSpeech(Protocol):
    def synthesize(
        self,
        *,
        text: str,
        audio_format: PlivoAudioFormat,
    ) -> AsyncIterator[bytes]: ...

    async def close(self) -> None: ...


class PlivoMediaTransport(Protocol):
    async def send_json(self, message: dict[str, Any]) -> None: ...


@runtime_checkable
class StartableProvider(Protocol):
    async def start(self) -> None: ...
