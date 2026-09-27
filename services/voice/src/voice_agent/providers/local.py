from __future__ import annotations

import asyncio
import math
import struct
from collections.abc import AsyncIterator

from voice_agent.providers.base import TranscriptEvent
from voice_agent.telephony.plivo import PlivoAudioFormat

MIN_PCM16_ACTIVITY_BYTES = 320
PCM16_ACTIVITY_AVERAGE_AMPLITUDE = 200.0


class LocalSpeechToText:
    """Deterministic speech recognizer for local smoke tests and unit tests."""

    def __init__(self, *, trigger_phrase: str = "hello") -> None:
        self._trigger_phrase = trigger_phrase
        self._closed = False
        self._events: asyncio.Queue[TranscriptEvent | None] = asyncio.Queue()

    async def accept_audio(self, audio: bytes) -> TranscriptEvent | None:
        if self._closed:
            return None
        decoded = audio.decode("utf-8", errors="ignore").lower()
        if self._trigger_phrase.lower() in decoded:
            return TranscriptEvent(
                text=decoded.strip(), is_final=True, confidence=1.0, speech_final=True
            )
        if _has_pcm16_activity(audio):
            return TranscriptEvent(
                text=self._trigger_phrase, is_final=True, confidence=1.0, speech_final=True
            )
        return None

    async def transcripts(self) -> AsyncIterator[TranscriptEvent]:
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event

    async def close(self) -> None:
        self._closed = True
        await self._events.put(None)


def _has_pcm16_activity(audio: bytes) -> bool:
    even_length = len(audio) - (len(audio) % 2)
    if even_length < MIN_PCM16_ACTIVITY_BYTES:
        return False
    sample_count = even_length // 2
    total_amplitude = 0
    for (sample,) in struct.iter_unpack("<h", audio[:even_length]):
        total_amplitude += abs(sample)
    return (total_amplitude / sample_count) >= PCM16_ACTIVITY_AVERAGE_AMPLITUDE


class LocalLLMClient:
    """Deterministic text generator for local smoke tests and unit tests."""

    def __init__(self, *, response_text: str = "I can help with that.") -> None:
        self._response_text = response_text

    async def generate_reply(self, *, call_id: str, user_text: str) -> AsyncIterator[str]:
        del call_id, user_text
        yield self._response_text

    async def close(self) -> None:
        return None


class LocalTextToSpeech:
    """Deterministic PCM16 tone generator for local smoke tests and unit tests."""

    async def synthesize(
        self,
        *,
        text: str,
        audio_format: PlivoAudioFormat,
    ) -> AsyncIterator[bytes]:
        del text
        if audio_format is not PlivoAudioFormat.L16_16KHZ:
            raise ValueError("LocalTextToSpeech only supports L16 16kHz audio.")
        sample_rate = audio_format.sample_rate
        duration_seconds = 0.12
        frequency_hz = 440.0
        amplitude = 2800
        frame_count = int(sample_rate * duration_seconds)
        samples = bytearray()
        for index in range(frame_count):
            value = int(amplitude * math.sin(2.0 * math.pi * frequency_hz * index / sample_rate))
            samples.extend(struct.pack("<h", value))
        yield bytes(samples)

    async def close(self) -> None:
        return None
