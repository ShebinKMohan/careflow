from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any
from urllib.parse import urlencode

import websockets

from voice_agent.providers.base import TranscriptEvent

logger = logging.getLogger(__name__)
DEFAULT_TRANSCRIPT_QUEUE_MAX_SIZE = 100
DEFAULT_TRANSCRIPT_MESSAGE_MAX_BYTES = 64 * 1024
DEFAULT_AUDIO_QUEUE_MAX_SIZE = 250
# Bound reconnect attempts after a send failure so a Deepgram outage cannot turn into a
# tight reconnect storm (and log flood). Capped exponential backoff per failure streak.
_RECONNECT_BACKOFF_BASE_SECONDS = 0.5
_RECONNECT_BACKOFF_MAX_SECONDS = 5.0


class DeepgramStreamingSpeechToText:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "nova-3",
        language: str = "en-IN",
        sample_rate: int = 16000,
        endpointing_ms: int = 150,
        utterance_end_ms: int = 1000,
        vad_events: bool = True,
        punctuate: bool = True,
        smart_format: bool = True,
        filler_words: bool = True,
        connect_timeout_seconds: float = 10.0,
        ping_interval_seconds: float = 20.0,
        ping_timeout_seconds: float = 10.0,
        transcript_queue_max_size: int = DEFAULT_TRANSCRIPT_QUEUE_MAX_SIZE,
        transcript_message_max_bytes: int = DEFAULT_TRANSCRIPT_MESSAGE_MAX_BYTES,
        audio_queue_max_size: int = DEFAULT_AUDIO_QUEUE_MAX_SIZE,
    ) -> None:
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive.")
        if endpointing_ms <= 0:
            raise ValueError("endpointing_ms must be positive.")
        if utterance_end_ms < 1000:
            raise ValueError("utterance_end_ms must be at least 1000 (Deepgram minimum).")
        if connect_timeout_seconds <= 0:
            raise ValueError("connect_timeout_seconds must be positive.")
        if ping_interval_seconds <= 0:
            raise ValueError("ping_interval_seconds must be positive.")
        if ping_timeout_seconds <= 0:
            raise ValueError("ping_timeout_seconds must be positive.")
        if transcript_queue_max_size <= 0:
            raise ValueError("transcript_queue_max_size must be positive.")
        if transcript_message_max_bytes <= 0:
            raise ValueError("transcript_message_max_bytes must be positive.")
        if audio_queue_max_size <= 0:
            raise ValueError("audio_queue_max_size must be positive.")
        self._api_key = _required_config_string(api_key, "api_key")
        self._model = _required_config_string(model, "model")
        self._language = _required_config_string(language, "language")
        self._sample_rate = sample_rate
        self._endpointing_ms = endpointing_ms
        self._utterance_end_ms = utterance_end_ms
        self._vad_events = vad_events
        self._punctuate = punctuate
        self._smart_format = smart_format
        self._filler_words = filler_words
        self._connect_timeout_seconds = connect_timeout_seconds
        self._ping_interval_seconds = ping_interval_seconds
        self._ping_timeout_seconds = ping_timeout_seconds
        self._transcript_queue_max_size = transcript_queue_max_size
        self._transcript_message_max_bytes = transcript_message_max_bytes
        self._audio_queue_max_size = audio_queue_max_size
        self._socket: Any | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._audio_sender_task: asyncio.Task[None] | None = None
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=audio_queue_max_size)
        self._transcripts: asyncio.Queue[TranscriptEvent | None] = asyncio.Queue(
            maxsize=transcript_queue_max_size
        )
        self._connection_lock = asyncio.Lock()
        self._closed = False

    async def start(self) -> None:
        if self._closed:
            return
        await self._ensure_connected()
        self._ensure_audio_sender_task()

    async def accept_audio(self, audio: bytes) -> TranscriptEvent | None:
        if self._closed:
            return None
        self._ensure_audio_sender_task()
        self._enqueue_audio(audio)
        await asyncio.sleep(0)
        return None

    async def transcripts(self) -> AsyncIterator[TranscriptEvent]:
        while True:
            event = await self._transcripts.get()
            if event is None:
                return
            yield event

    async def close(self) -> None:
        self._closed = True
        await self._cancel_audio_sender_task()
        if self._socket is not None:
            with suppress(Exception):
                await self._socket.send(json.dumps({"type": "CloseStream"}))
            await self._socket.close()
        if self._reader_task is not None:
            self._reader_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._reader_task
        self._enqueue_transcript(None)

    def _ensure_audio_sender_task(self) -> None:
        if self._audio_sender_task is not None and not self._audio_sender_task.done():
            return
        if self._audio_sender_task is not None:
            self._consume_audio_sender_result(self._audio_sender_task)
        self._audio_sender_task = asyncio.create_task(self._send_audio_loop())

    async def _cancel_audio_sender_task(self) -> None:
        if self._audio_sender_task is None or self._audio_sender_task.done():
            if self._audio_sender_task is not None:
                self._consume_audio_sender_result(self._audio_sender_task)
            return
        self._audio_sender_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._audio_sender_task

    def _consume_audio_sender_result(self, task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.warning("Deepgram audio sender exited unexpectedly.", exc_info=True)

    def _enqueue_audio(self, audio: bytes) -> None:
        try:
            self._audio_queue.put_nowait(audio)
            return
        except asyncio.QueueFull:
            try:
                _ = self._audio_queue.get_nowait()
                self._audio_queue.task_done()
            except asyncio.QueueEmpty:
                pass
        try:
            self._audio_queue.put_nowait(audio)
        except asyncio.QueueFull:
            logger.warning("Dropped Deepgram audio frame because the send queue is full.")

    async def _send_audio_loop(self) -> None:
        consecutive_failures = 0
        while not self._closed:
            audio = await self._audio_queue.get()
            send_failed = False
            try:
                socket = await self._ensure_connected()
                await socket.send(audio)
                consecutive_failures = 0
            except asyncio.CancelledError:
                raise
            except Exception:
                send_failed = True
                consecutive_failures += 1
                # Log once per failure streak to avoid flooding logs during an outage.
                if not self._closed and consecutive_failures == 1:
                    logger.exception("Deepgram audio send failed; backing off reconnects.")
            finally:
                self._audio_queue.task_done()
            if send_failed and not self._closed:
                backoff = min(
                    _RECONNECT_BACKOFF_BASE_SECONDS * (1 << min(consecutive_failures - 1, 5)),
                    _RECONNECT_BACKOFF_MAX_SECONDS,
                )
                await asyncio.sleep(backoff)

    async def _ensure_connected(self) -> Any:
        if (
            self._socket is not None
            and self._reader_task is not None
            and not self._reader_task.done()
        ):
            return self._socket
        async with self._connection_lock:
            if (
                self._socket is not None
                and self._reader_task is not None
                and not self._reader_task.done()
            ):
                return self._socket
            if self._socket is not None:
                with suppress(Exception):
                    await self._socket.close()
                self._socket = None
                self._reader_task = None
            params = urlencode(
                {
                    "model": self._model,
                    "language": self._language,
                    "encoding": "linear16",
                    "sample_rate": str(self._sample_rate),
                    "channels": "1",
                    "interim_results": "true",
                    "endpointing": str(self._endpointing_ms),
                    # UtteranceEnd is a silence-gap fallback for end-of-turn; it needs
                    # interim_results=true and a value of at least 1000ms.
                    "utterance_end_ms": str(self._utterance_end_ms),
                    "vad_events": _bool_param(self._vad_events),
                    "punctuate": _bool_param(self._punctuate),
                    "smart_format": _bool_param(self._smart_format),
                    "filler_words": _bool_param(self._filler_words),
                }
            )
            self._socket = await websockets.connect(
                f"wss://api.deepgram.com/v1/listen?{params}",
                additional_headers={"Authorization": f"Token {self._api_key}"},
                open_timeout=self._connect_timeout_seconds,
                ping_interval=self._ping_interval_seconds,
                ping_timeout=self._ping_timeout_seconds,
                max_size=self._transcript_message_max_bytes,
            )
            self._reader_task = asyncio.create_task(self._read_transcripts(self._socket))
            return self._socket

    async def _read_transcripts(self, socket: Any) -> None:
        try:
            async for message in socket:
                if not isinstance(message, str):
                    continue
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    logger.warning("Deepgram returned a non-JSON transcript message.")
                    continue
                if not isinstance(data, dict):
                    continue
                message_type = data.get("type")
                if message_type == "UtteranceEnd":
                    # Silence-gap end-of-turn signal. last_word_end == -1 means the
                    # gap was already covered by a final result, so disregard it.
                    if data.get("last_word_end") != -1:
                        self._enqueue_transcript(
                            TranscriptEvent(text="", is_final=False, speech_final=True)
                        )
                    continue
                if message_type == "SpeechStarted":
                    continue
                event = _extract_deepgram_transcript(data)
                if event is not None:
                    self._enqueue_transcript(event)
        except Exception:
            if not self._closed:
                logger.exception("Deepgram transcript reader exited unexpectedly.")
        finally:
            current_socket = self._socket
            if current_socket is not None and current_socket is socket:
                self._socket = None
                self._reader_task = None
                if not self._closed:
                    with suppress(Exception):
                        await current_socket.close()

    def _enqueue_transcript(self, event: TranscriptEvent | None) -> None:
        while True:
            try:
                self._transcripts.put_nowait(event)
                return
            except asyncio.QueueFull:
                try:
                    dropped = self._transcripts.get_nowait()
                except asyncio.QueueEmpty:
                    continue
                if dropped is not None:
                    logger.warning("Dropped stale Deepgram transcript event from full queue.")


def _extract_deepgram_transcript(data: dict[str, Any]) -> TranscriptEvent | None:
    channel = data.get("channel")
    if not isinstance(channel, dict):
        return None
    alternatives = channel.get("alternatives")
    if not isinstance(alternatives, list) or not alternatives:
        return None
    first = alternatives[0]
    if not isinstance(first, dict):
        return None
    text = first.get("transcript")
    if not isinstance(text, str) or not text.strip():
        return None
    # Keep is_final and speech_final distinct: is_final = "segment finalized",
    # speech_final = "end of the caller's turn". The runner accumulates is_final
    # segments and only commits a turn on speech_final / UtteranceEnd.
    is_final = bool(data.get("is_final"))
    speech_final = bool(data.get("speech_final"))
    confidence = first.get("confidence")
    return TranscriptEvent(
        text=text.strip(),
        is_final=is_final,
        confidence=confidence if isinstance(confidence, int | float) else None,
        speech_final=speech_final,
    )


def _bool_param(value: bool) -> str:
    return "true" if value else "false"


def _required_config_string(value: str, name: str) -> str:
    clean_value = value.strip()
    if not clean_value:
        raise ValueError(f"{name} must not be blank.")
    return clean_value
