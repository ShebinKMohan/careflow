"""`KioskTurnLoop`: one kiosk voice session, from microphone frames to the next spoken question.

- Audio frames go to the STT. On a final transcript the loop posts the turn to the CareFlow API
  (`input_mode: voice`), sends the kiosk the transcript and the next question, and speaks it
  with Kokoro (PCM16 at 24 kHz, in 100 ms frames).
- While the question plays, frames go to a Silero VAD instead of the STT. Sustained speech
  (barge-in) stops playback, tells the kiosk to drop queued audio and hands the audio that
  triggered it to the STT as the start of the patient's answer.
- Each turn's latency (end of speech → transcript → API response → first TTS byte) is logged
  and recorded as a `kiosk_turn_latency` event for `GET /latency/summary`. Only timings are
  recorded: never audio, and never the transcript (it lives in the API session only).

This is deliberately not the agent's `MediaSessionRunner`, which is Plivo- and LLM-centric.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Final, Protocol

import numpy as np
from numpy.typing import NDArray

from careflow_contracts.api_models import TurnResponse
from voice_agent.kiosk.auth import PatientSession
from voice_agent.kiosk.protocol import (
    CLOSE_SERVER_ERROR,
    OUTPUT_SAMPLE_RATE,
    TTS_FRAME_BYTES,
    error_message,
    question_message,
    text_message,
    tts_end_message,
    tts_start_message,
)
from voice_agent.kiosk.turns import TurnError, TurnPoster
from voice_agent.latency import KIOSK_TURN_LATENCY_EVENT, summarize_kiosk_turn_latency_events
from voice_agent.providers._silero_vad import VAD_FRAME_SAMPLES
from voice_agent.providers.base import (
    SpeechToText,
    StartableProvider,
    TextToSpeech,
    TranscriptEvent,
)
from voice_agent.providers.mlx_whisper_stt import VoiceActivity
from voice_agent.storage.call_events import CallEventStore
from voice_agent.telephony.plivo import PlivoAudioFormat

logger = logging.getLogger(__name__)

BARGE_IN_PROBABILITY: Final = 0.5
BARGE_IN_FRAMES: Final = 6  # ~190 ms of speech (32 ms VAD frames) while the question plays
PLAYBACK_MARGIN_SECONDS: Final = 0.25
REPLAY_FRAMES: Final = 25  # the last ~0.5 s of 20 ms frames starts the answer after a barge-in


class KioskSender(Protocol):
    async def send_json(self, message: dict[str, Any]) -> None: ...

    async def send_bytes(self, data: bytes) -> None: ...

    async def close(self, code: int) -> None: ...


@dataclass
class TurnTiming:
    """Monotonic timestamps of one turn."""

    transcript_at: float
    speech_ended_at: float | None = None
    api_response_at: float | None = None
    first_tts_byte_at: float | None = None

    def event(self, session_id: str) -> dict[str, Any]:
        return {
            "event": KIOSK_TURN_LATENCY_EVENT,
            "session_id": session_id,
            "speech_end_to_transcript_ms": _ms(self.speech_ended_at, self.transcript_at),
            "transcript_to_api_response_ms": _ms(self.transcript_at, self.api_response_at),
            "api_response_to_first_tts_byte_ms": _ms(self.api_response_at, self.first_tts_byte_at),
            "speech_end_to_first_tts_byte_ms": _ms(self.speech_ended_at, self.first_tts_byte_at),
        }


def _ms(start: float | None, end: float | None) -> float | None:
    if start is None or end is None:
        return None
    return round(max(0.0, end - start) * 1000.0, 1)


def next_prompt(reply: TurnResponse) -> tuple[str, dict[str, Any] | None, bool]:
    """(what to say, the question for the kiosk screen, whether the intake is done).

    Mirrors the kiosk's fallbacks (ADR-027): an absent `next_question` means an older API, so
    `assistant_text` is the question; an explicit null means the conversation is done.
    """
    if "next_question" in reply.model_fields_set:
        question = reply.next_question
        if question is None:
            return reply.assistant_text.strip(), None, True
        spoken = f"{question.notice} {question.text}" if question.notice else question.text
        # The question as the API sent it: newer fields (notice_kind, M2-10) pass through.
        return spoken, question.model_dump(mode="json", exclude_unset=True), False
    if reply.done:
        return reply.assistant_text.strip(), None, True
    fallback = {
        "question_id": reply.turn_id,
        "text": reply.assistant_text,
        "key": None,
        "notice": None,
    }
    return reply.assistant_text.strip(), fallback, False


class KioskTurnLoop:
    def __init__(
        self,
        *,
        session: PatientSession,
        stt: SpeechToText,
        tts: TextToSpeech,
        vad: VoiceActivity,
        turns: TurnPoster,
        sender: KioskSender,
        events: CallEventStore | None = None,
        language: str = "en",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session = session
        self._stt = stt
        self._tts = tts
        self._vad = vad
        self._turns = turns
        self._sender = sender
        self._events = events
        self._language = language
        self._clock = clock
        self._pending: asyncio.Queue[TranscriptEvent | None] = asyncio.Queue()
        self._tasks: list[asyncio.Task[None]] = []
        self._tts_task: asyncio.Task[None] | None = None
        self._send_lock = asyncio.Lock()
        self._playback_until = 0.0
        self._vad_pending: NDArray[np.float32] = np.empty(0, dtype=np.float32)
        self._speech_run = 0
        self._recent: deque[bytes] = deque(maxlen=REPLAY_FRAMES)
        self._segments: list[str] = []
        self._timings: list[dict[str, Any]] = []
        self._closed = False

    @property
    def turn_latencies(self) -> list[dict[str, Any]]:
        return list(self._timings)

    async def start(self) -> None:
        """Load the engines (the STT raises if it cannot), then start listening."""
        for provider in (self._stt, self._tts):
            if isinstance(provider, StartableProvider):
                await provider.start()
        self._vad.reset()
        self._tasks.append(asyncio.create_task(self._pump(), name="kiosk-stt-pump"))
        self._tasks.append(asyncio.create_task(self._consume(), name="kiosk-turns"))

    async def feed(self, frame: bytes) -> None:
        if self._closed or not frame:
            return
        self._recent.append(frame)
        if self._speaking():
            if self._barge_in(frame):
                await self._interrupt()
            return
        event = await self._stt.accept_audio(frame)
        if event is not None:
            self._pending.put_nowait(event)

    async def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._cancel_speech()
        with suppress(Exception):
            await self._stt.close()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with suppress(asyncio.CancelledError, Exception):
                await task
        with suppress(Exception):
            await self._tts.close()
        if self._timings:
            summary = summarize_kiosk_turn_latency_events(self._timings)
            total = summary["stages"]["total"]
            logger.info(
                "Kiosk voice session %s: %d turns, end of speech to first TTS byte "
                "p50 %s ms, p95 %s ms",
                self._session.session_id,
                summary["turn_count"],
                total["p50_ms"],
                total["p95_ms"],
            )

    # --- listening ---------------------------------------------------------------------------

    async def _pump(self) -> None:
        async for event in self._stt.transcripts():
            self._pending.put_nowait(event)
        self._pending.put_nowait(None)
        if not self._closed:
            # The speech engine stopped mid-session: say so and close, as a failed start does,
            # so the kiosk falls back to typing instead of listening to nothing.
            logger.warning("Kiosk STT for %s stopped mid-session.", self._session.session_id)
            await self._send(error_message("speech_unavailable"))
            async with self._send_lock:
                with suppress(Exception):
                    await self._sender.close(CLOSE_SERVER_ERROR)

    async def _consume(self) -> None:
        while (event := await self._pending.get()) is not None:
            if not event.is_final:
                if event.text.strip():
                    await self._send(text_message("partial", event.text))
                continue
            if event.text.strip():
                self._segments.append(event.text.strip())
            if not event.speech_final or not self._segments:
                continue
            text = " ".join(self._segments)
            self._segments.clear()
            await self._handle_turn(text, event.speech_ended_at)

    async def _handle_turn(self, text: str, speech_ended_at: float | None) -> None:
        timing = TurnTiming(transcript_at=self._clock(), speech_ended_at=speech_ended_at)
        await self._send(text_message("final", text))
        try:
            reply = await self._turns.post(self._session, text=text, language=self._language)
        except TurnError as exc:
            logger.warning("Kiosk turn for %s failed: %s", self._session.session_id, exc.code)
            await self._send(error_message(exc.code))
            return
        timing.api_response_at = self._clock()
        spoken, question, done = next_prompt(reply)
        await self._send(question_message(spoken, question, done=done))
        await self._cancel_speech()
        if spoken:
            self._tts_task = asyncio.create_task(self._speak(spoken, timing), name="kiosk-tts")
        else:
            await self._record(timing)

    # --- speaking ----------------------------------------------------------------------------

    def _speaking(self) -> bool:
        return self._clock() < self._playback_until

    async def _speak(self, text: str, timing: TurnTiming) -> None:
        started = False
        playback_start = 0.0
        audio_seconds = 0.0
        try:
            async for chunk in self._tts.synthesize(
                text=text, audio_format=PlivoAudioFormat.L16_24KHZ
            ):
                if not chunk:
                    continue
                if not started:
                    started = True
                    playback_start = self._clock()
                    timing.first_tts_byte_at = playback_start
                    self._recent.clear()
                    self._reset_barge_in()
                    await self._send(tts_start_message())
                    await self._record(timing)
                audio_seconds += len(chunk) / 2 / OUTPUT_SAMPLE_RATE
                self._playback_until = playback_start + audio_seconds + PLAYBACK_MARGIN_SECONDS
                for offset in range(0, len(chunk), TTS_FRAME_BYTES):
                    await self._send_bytes(chunk[offset : offset + TTS_FRAME_BYTES])
            if started:
                await self._send(tts_end_message(interrupted=False))
            else:
                await self._record(timing)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Kiosk TTS failed for %s.", self._session.session_id)
            self._playback_until = 0.0
            await self._send(error_message("tts_failed"))
            if not started:
                await self._record(timing)

    async def _cancel_speech(self) -> None:
        task, self._tts_task = self._tts_task, None
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    def _barge_in(self, frame: bytes) -> bool:
        usable = len(frame) - (len(frame) % 2)
        samples = np.frombuffer(frame[:usable], dtype="<i2").astype(np.float32) / 32768.0
        self._vad_pending = np.concatenate([self._vad_pending, samples])
        triggered = False
        while len(self._vad_pending) >= VAD_FRAME_SAMPLES:
            window = self._vad_pending[:VAD_FRAME_SAMPLES]
            self._vad_pending = self._vad_pending[VAD_FRAME_SAMPLES:]
            if self._vad.process_frame(window) >= BARGE_IN_PROBABILITY:
                self._speech_run += 1
            else:
                self._speech_run = 0
            triggered = triggered or self._speech_run >= BARGE_IN_FRAMES
        return triggered

    def _reset_barge_in(self) -> None:
        self._vad.reset()
        self._vad_pending = np.empty(0, dtype=np.float32)
        self._speech_run = 0

    async def _interrupt(self) -> None:
        """Barge-in: stop the question and treat the triggering audio as the answer's start."""
        self._playback_until = 0.0
        self._reset_barge_in()
        await self._cancel_speech()
        await self._send(tts_end_message(interrupted=True))
        replay = list(self._recent)
        self._recent.clear()
        for frame in replay:
            event = await self._stt.accept_audio(frame)
            if event is not None:
                self._pending.put_nowait(event)

    # --- output ------------------------------------------------------------------------------

    async def _record(self, timing: TurnTiming) -> None:
        event = timing.event(self._session.session_id)
        self._timings.append(event)
        logger.info(
            "Kiosk turn latency (ms): speech end to transcript %s, transcript to API %s, "
            "API to first TTS byte %s, total %s",
            event["speech_end_to_transcript_ms"],
            event["transcript_to_api_response_ms"],
            event["api_response_to_first_tts_byte_ms"],
            event["speech_end_to_first_tts_byte_ms"],
        )
        if self._events is not None:
            try:
                await self._events.record(event)
            except Exception:
                logger.exception("Could not record a kiosk turn latency event.")

    async def _send(self, message: dict[str, Any]) -> None:
        async with self._send_lock:
            with suppress(Exception):  # the kiosk went away; stop() follows from the receiver
                await self._sender.send_json(message)

    async def _send_bytes(self, data: bytes) -> None:
        async with self._send_lock:
            with suppress(Exception):
                await self._sender.send_bytes(data)
