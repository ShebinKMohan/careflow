from __future__ import annotations

import asyncio
import importlib
import json
import logging
import time
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol

from voice_agent.media.session import (
    HandoffTarget,
    VoiceTurnLatencySnapshot,
    _handoff_match_score,
    _handoff_tokens,
)
from voice_agent.providers.base import PlivoMediaTransport
from voice_agent.telephony.plivo import PlivoAudioFormat, play_audio_message

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GoogleLiveRealtimeEvent:
    audio: bytes | None = None
    input_text: str | None = None
    output_text: str | None = None


class RealtimeVoiceClient(Protocol):
    async def start(self, *, system_instruction: str | None = None) -> None: ...

    async def send_text(self, text: str) -> None: ...

    async def send_turn_text(self, text: str) -> None: ...

    async def accept_audio(self, audio: bytes) -> None: ...

    async def end_audio_stream(self) -> None: ...

    def events(self) -> AsyncIterator[GoogleLiveRealtimeEvent]: ...

    async def close(self) -> None: ...


class GoogleLiveRealtimeClient:
    """Google Gemini Live API adapter.

    The Live API owns the speech loop: caller PCM goes to Google directly and model PCM
    comes back directly, so this client deliberately does not expose STT or TTS adapters.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        voice: str | None = None,
        temperature: float | None = None,
        input_sample_rate_hz: int = 16000,
        output_sample_rate_hz: int = 24000,
        connect_timeout_seconds: float = 10.0,
        event_queue_max_size: int = 100,
    ) -> None:
        self._api_key = _required_config_string(api_key, "api_key")
        self._model = _required_config_string(model, "model")
        self._voice = _optional_config_string(voice)
        self._temperature = temperature
        if input_sample_rate_hz <= 0:
            raise ValueError("input_sample_rate_hz must be positive.")
        if output_sample_rate_hz <= 0:
            raise ValueError("output_sample_rate_hz must be positive.")
        if connect_timeout_seconds <= 0:
            raise ValueError("connect_timeout_seconds must be positive.")
        if event_queue_max_size <= 0:
            raise ValueError("event_queue_max_size must be positive.")
        self._input_sample_rate_hz = input_sample_rate_hz
        self._output_sample_rate_hz = output_sample_rate_hz
        self._connect_timeout_seconds = connect_timeout_seconds
        self._events: asyncio.Queue[GoogleLiveRealtimeEvent | None] = asyncio.Queue(
            maxsize=event_queue_max_size
        )
        self._client: Any | None = None
        self._session_context: Any | None = None
        self._session: Any | None = None
        self._types: Any | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._closed = False
        self._start_lock = asyncio.Lock()

    async def start(self, *, system_instruction: str | None = None) -> None:
        if self._closed:
            return
        if self._session is not None:
            return
        async with self._start_lock:
            if self._session is not None:
                return
            genai, types = _load_google_genai()
            self._types = types
            self._client = genai.Client(api_key=self._api_key)
            config = _live_connect_config(
                system_instruction=system_instruction,
                voice=self._voice,
                temperature=self._temperature,
            )
            connect = self._client.aio.live.connect(model=self._model, config=config)
            self._session_context = connect
            self._session = await asyncio.wait_for(
                connect.__aenter__(),
                timeout=self._connect_timeout_seconds,
            )
            self._reader_task = asyncio.create_task(self._read_event_loop())

    async def send_text(self, text: str) -> None:
        clean_text = text.strip()
        if not clean_text:
            return
        session = await self._ensure_session()
        await session.send_realtime_input(text=clean_text)

    async def send_turn_text(self, text: str) -> None:
        clean_text = text.strip()
        if not clean_text:
            return
        session = await self._ensure_session()
        if self._types is None:
            raise RuntimeError("Google Live session was not started.")
        await session.send_client_content(
            turns=self._types.Content(
                role="user",
                parts=[self._types.Part(text=clean_text)],
            ),
            turn_complete=True,
        )

    async def accept_audio(self, audio: bytes) -> None:
        if not audio or self._closed:
            return
        session = await self._ensure_session()
        if self._types is None:
            raise RuntimeError("Google Live session was not started.")
        blob = self._types.Blob(
            data=audio,
            mime_type=f"audio/pcm;rate={self._input_sample_rate_hz}",
        )
        await session.send_realtime_input(audio=blob)

    async def end_audio_stream(self) -> None:
        if self._closed:
            return
        session = await self._ensure_session()
        await session.send_realtime_input(audio_stream_end=True)

    async def events(self) -> AsyncIterator[GoogleLiveRealtimeEvent]:
        while True:
            item = await self._events.get()
            if item is None:
                return
            yield item

    async def close(self) -> None:
        self._closed = True
        if self._reader_task is not None:
            self._reader_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._reader_task
        if self._session_context is not None:
            with suppress(Exception):
                await self._session_context.__aexit__(None, None, None)
        self._enqueue_event(None)

    async def _ensure_session(self) -> Any:
        await self.start()
        if self._session is None:
            raise RuntimeError("Google Live session is not connected.")
        return self._session

    async def _read_event_loop(self) -> None:
        if self._session is None:
            return
        try:
            async for response in self._session.receive():
                for event in _extract_realtime_events(response):
                    self._enqueue_event(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            if not self._closed:
                logger.exception("Google Live audio receive loop failed.")
        finally:
            self._enqueue_event(None)

    def _enqueue_event(self, event: GoogleLiveRealtimeEvent | None) -> None:
        if event is not None and not event.audio and not event.input_text and not event.output_text:
            return
        try:
            self._events.put_nowait(event)
            return
        except asyncio.QueueFull:
            with suppress(asyncio.QueueEmpty):
                self._events.get_nowait()
                self._events.task_done()
        with suppress(asyncio.QueueFull):
            self._events.put_nowait(event)


class GoogleLiveMediaSessionRunner:
    def __init__(
        self,
        *,
        call_id: str,
        stream_id: str,
        transport: PlivoMediaTransport,
        client: RealtimeVoiceClient,
        audio_format: PlivoAudioFormat,
        system_instruction: str | None = None,
        greeting: str | None = None,
        google_output_sample_rate_hz: int = 24000,
        audio_ingest_timeout_seconds: float = 1.0,
        plivo_send_timeout_seconds: float = 1.0,
        provider_close_timeout_seconds: float = 2.0,
        max_play_audio_bytes: int = 6400,
        latency_observer: Callable[[VoiceTurnLatencySnapshot], object] | None = None,
        transcript_observer: Callable[[str], object] | None = None,
        handoff_targets: Sequence[HandoffTarget] | None = None,
        handoff_client_factory: Callable[
            [HandoffTarget, str], tuple[RealtimeVoiceClient, str | None]
        ]
        | None = None,
    ) -> None:
        if google_output_sample_rate_hz <= 0:
            raise ValueError("google_output_sample_rate_hz must be positive.")
        if audio_ingest_timeout_seconds <= 0:
            raise ValueError("audio_ingest_timeout_seconds must be positive.")
        if plivo_send_timeout_seconds <= 0:
            raise ValueError("plivo_send_timeout_seconds must be positive.")
        if provider_close_timeout_seconds <= 0:
            raise ValueError("provider_close_timeout_seconds must be positive.")
        if max_play_audio_bytes < 2:
            raise ValueError("max_play_audio_bytes must be at least 2.")
        self._call_id = call_id
        self._stream_id = stream_id
        self._transport = transport
        self._client = client
        self._audio_format = audio_format
        self._system_instruction = _optional_config_string(system_instruction)
        self._greeting = _optional_config_string(greeting)
        self._google_output_sample_rate_hz = google_output_sample_rate_hz
        self._audio_ingest_timeout_seconds = audio_ingest_timeout_seconds
        self._plivo_send_timeout_seconds = plivo_send_timeout_seconds
        self._provider_close_timeout_seconds = provider_close_timeout_seconds
        self._max_play_audio_bytes = (
            max_play_audio_bytes if max_play_audio_bytes % 2 == 0 else (max_play_audio_bytes - 1)
        )
        self._latency_observer = latency_observer
        self._transcript_observer = transcript_observer
        self._handoff_targets = tuple(
            target for target in (handoff_targets or ()) if target.target_id and target.condition
        )
        self._handoff_client_factory = handoff_client_factory
        self._active_handoff_target_id: str | None = None
        self._transcript_turns: list[dict[str, str | int]] = []
        self._transcript_turn_index = 0
        self._media_start_at: float | None = None
        self._first_audio_in_at: float | None = None
        self._last_voice_frame_at: float | None = None
        self._current_turn_epoch = 0
        self._current_turn_committed_at: float | None = None
        self._current_turn_transcript_chars = 0
        self._first_google_audio_at: float | None = None
        self._first_play_audio_at: float | None = None
        self._latency_emitted_for_turn = 0
        self._audio_task: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def call_id(self) -> str:
        return self._call_id

    @property
    def stream_id(self) -> str:
        return self._stream_id

    async def start(self) -> None:
        if self._closed:
            return
        if self._media_start_at is None:
            self._media_start_at = time.perf_counter()
        await self._client.start(system_instruction=self._system_instruction)
        if self._audio_task is None or self._audio_task.done():
            self._audio_task = asyncio.create_task(self._play_google_audio_loop())

    async def greet(self) -> None:
        # Caller-first by design for Google Live: the Live API becomes unreliable when a
        # model-generated greeting is sent before realtime caller audio. The standard
        # STT/TTS runner still supports immediate cached greetings.
        return None

    def notify_played_stream(self, name: str | None) -> None:
        # The Google Live runner uses its own playback path and does not emit Plivo
        # checkpoints; playedStream acks are a no-op here.
        del name

    async def accept_audio(self, audio: bytes) -> None:
        if self._closed:
            return
        now = time.perf_counter()
        if self._first_audio_in_at is None:
            self._first_audio_in_at = now
        self._last_voice_frame_at = now
        await asyncio.wait_for(
            self._client.accept_audio(audio),
            timeout=self._audio_ingest_timeout_seconds,
        )

    async def end_audio_stream(self) -> None:
        await self._client.end_audio_stream()

    async def close(self) -> None:
        self._closed = True
        await asyncio.wait_for(
            self._client.close(),
            timeout=self._provider_close_timeout_seconds,
        )
        if self._audio_task is not None:
            with suppress(asyncio.CancelledError):
                await self._audio_task

    async def _play_google_audio_loop(self) -> None:
        while True:
            client = self._client
            async for event in client.events():
                if event.input_text:
                    await self._handle_input_transcript(event.input_text)
                    if self._client is not client:
                        break
                if self._client is not client:
                    break
                if event.audio:
                    await self._play_google_audio(event.audio)
                if event.output_text:
                    self._record_output_transcript(event.output_text)
            if self._client is client:
                return

    async def _handle_input_transcript(self, text: str) -> None:
        self._record_input_transcript(text)
        await self._activate_matching_handoff(text)

    async def _activate_matching_handoff(self, user_text: str) -> None:
        target = self._matching_handoff_target(user_text)
        if target is None or self._handoff_client_factory is None:
            return
        context = self._handoff_conversation_context()
        try:
            replacement, system_instruction = self._handoff_client_factory(target, context)
            await replacement.start(system_instruction=system_instruction)
            await replacement.send_turn_text(
                f"Continue the live phone call as {target.name}. "
                f"The latest caller request is: {user_text.strip()}"
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Google Live handoff failed before activation: target_id=%s "
                "call_id=%s stream_id=%s.",
                target.target_id,
                self._call_id,
                self._stream_id,
                exc_info=True,
            )
            return
        previous_client = self._client
        self._client = replacement
        self._active_handoff_target_id = target.target_id
        await previous_client.close()

    def _matching_handoff_target(self, user_text: str) -> HandoffTarget | None:
        if not self._handoff_targets or self._handoff_client_factory is None:
            return None
        user_tokens = _handoff_tokens(user_text)
        if not user_tokens:
            return None
        best_target: HandoffTarget | None = None
        best_score = 0
        for target in self._handoff_targets:
            if target.target_id == self._active_handoff_target_id:
                continue
            score = _handoff_match_score(user_tokens, target)
            if score > best_score:
                best_score = score
                best_target = target
        return best_target if best_score > 0 else None

    def _handoff_conversation_context(self) -> str:
        if not self._transcript_turns:
            return ""
        lines: list[str] = []
        for turn in self._transcript_turns[-12:]:
            role = str(turn.get("role", "")).strip()
            text = str(turn.get("text", "")).strip()
            if role and text:
                lines.append(f"{role}: {text}")
        return "\n".join(lines)

    async def _play_google_audio(self, audio: bytes) -> None:
        if self._first_google_audio_at is None:
            self._first_google_audio_at = time.perf_counter()
        converted = convert_pcm16_sample_rate(
            audio,
            from_hz=self._google_output_sample_rate_hz,
            to_hz=self._audio_format.sample_rate,
        )
        for chunk in _even_chunks(converted, max_bytes=self._max_play_audio_bytes):
            await asyncio.wait_for(
                self._transport.send_json(play_audio_message(chunk, self._audio_format)),
                timeout=self._plivo_send_timeout_seconds,
            )
            if self._first_play_audio_at is None:
                self._first_play_audio_at = time.perf_counter()
                self._emit_latency_if_ready()

    def _record_input_transcript(self, text: str) -> None:
        if not text.strip():
            return
        if self._current_turn_epoch == 0 or self._turn_has_role(
            "assistant", self._current_turn_epoch
        ):
            self._transcript_turn_index += 1
            self._current_turn_epoch = self._transcript_turn_index
            self._current_turn_committed_at = time.perf_counter()
            self._first_google_audio_at = None
            self._first_play_audio_at = None
        self._append_transcript_turn("user", text, turn=self._current_turn_epoch)
        self._current_turn_transcript_chars = len(self._turn_text("user", self._current_turn_epoch))

    def _record_output_transcript(self, text: str) -> None:
        if not text.strip():
            return
        turn = self._current_turn_epoch or self._transcript_turn_index
        self._append_transcript_turn("assistant", text, turn=turn)

    def _append_transcript_turn(self, role: str, text: str, *, turn: int) -> None:
        existing = self._turn_for(role, turn)
        if existing is None:
            self._transcript_turns.append(
                {"role": role, "text": _normalize_transcript_text(text), "turn": turn}
            )
        else:
            existing["text"] = _normalize_transcript_text(f"{existing['text']}{text}")
        observer = self._transcript_observer
        if observer is None:
            return
        observer(json.dumps(self._transcript_turns, ensure_ascii=False))

    def _turn_for(self, role: str, turn: int) -> dict[str, str | int] | None:
        for item in self._transcript_turns:
            if item.get("role") == role and item.get("turn") == turn:
                return item
        return None

    def _turn_has_role(self, role: str, turn: int) -> bool:
        return self._turn_for(role, turn) is not None

    def _turn_text(self, role: str, turn: int) -> str:
        item = self._turn_for(role, turn)
        if item is None:
            return ""
        return str(item.get("text", ""))

    def _emit_latency_if_ready(self) -> None:
        observer = self._latency_observer
        if observer is None:
            return
        turn_epoch = self._current_turn_epoch or 1
        if self._latency_emitted_for_turn == turn_epoch:
            return
        self._latency_emitted_for_turn = turn_epoch
        turn_committed_at = (
            self._current_turn_committed_at
            or self._last_voice_frame_at
            or self._first_audio_in_at
            or self._media_start_at
        )
        if (
            turn_committed_at is not None
            and self._last_voice_frame_at is not None
            and turn_committed_at < self._last_voice_frame_at
        ):
            turn_committed_at = self._last_voice_frame_at
        observer(
            VoiceTurnLatencySnapshot(
                call_id=self._call_id,
                stream_id=self._stream_id,
                turn_epoch=turn_epoch,
                transcript_chars=self._current_turn_transcript_chars,
                turn_commit_source=(
                    "google_live_input_transcription"
                    if self._current_turn_committed_at is not None
                    else "google_live_audio"
                ),
                response_plan_action="google_live_realtime",
                response_plan_confidence=None,
                response_plan_reason=None,
                media_start_to_first_audio_ms=_elapsed_ms(
                    self._media_start_at,
                    self._first_audio_in_at,
                ),
                first_audio_to_last_voice_frame_ms=_elapsed_ms(
                    self._first_audio_in_at,
                    self._last_voice_frame_at,
                ),
                last_voice_frame_to_turn_committed_ms=_elapsed_ms(
                    self._last_voice_frame_at,
                    turn_committed_at,
                ),
                last_voice_frame_to_first_play_audio_ms=_elapsed_ms(
                    self._last_voice_frame_at,
                    self._first_play_audio_at,
                ),
                first_audio_to_speech_final_ms=None,
                speech_final_to_turn_committed_ms=None,
                turn_committed_to_llm_first_chunk_ms=_elapsed_ms(
                    turn_committed_at,
                    self._first_google_audio_at,
                ),
                turn_committed_to_first_reply_chunk_ms=None,
                llm_first_chunk_to_first_reply_chunk_ms=None,
                llm_first_chunk_to_tts_first_audio_ms=None,
                first_reply_chunk_to_tts_first_audio_ms=None,
                tts_first_audio_to_first_play_audio_ms=_elapsed_ms(
                    self._first_google_audio_at,
                    self._first_play_audio_at,
                ),
                turn_committed_to_first_play_audio_ms=_elapsed_ms(
                    turn_committed_at,
                    self._first_play_audio_at,
                ),
                first_audio_to_first_play_audio_ms=_elapsed_ms(
                    self._first_audio_in_at,
                    self._first_play_audio_at,
                ),
                answer_first_play_audio_to_llm_stream_completed_ms=None,
                turn_committed_to_answer_first_play_audio_ms=_elapsed_ms(
                    turn_committed_at,
                    self._first_play_audio_at,
                ),
            )
        )


def convert_pcm16_sample_rate(audio: bytes, *, from_hz: int, to_hz: int) -> bytes:
    if from_hz <= 0:
        raise ValueError("from_hz must be positive.")
    if to_hz <= 0:
        raise ValueError("to_hz must be positive.")
    if len(audio) % 2:
        raise ValueError("PCM16 audio must contain complete samples.")
    if from_hz == to_hz or not audio:
        return audio
    samples = [
        int.from_bytes(audio[i : i + 2], "little", signed=True) for i in range(0, len(audio), 2)
    ]
    output_count = max(1, int(len(samples) * to_hz / from_hz))
    output = bytearray()
    for out_index in range(output_count):
        source_pos = out_index * from_hz / to_hz
        left_index = min(int(source_pos), len(samples) - 1)
        right_index = min(left_index + 1, len(samples) - 1)
        fraction = source_pos - left_index
        sample = round(samples[left_index] * (1.0 - fraction) + samples[right_index] * fraction)
        sample = max(-32768, min(32767, sample))
        output.extend(sample.to_bytes(2, "little", signed=True))
    return bytes(output)


def _even_chunks(audio: bytes, *, max_bytes: int) -> Iterator[bytes]:
    chunk_size = max(2, max_bytes if max_bytes % 2 == 0 else max_bytes - 1)
    for index in range(0, len(audio), chunk_size):
        chunk = audio[index : index + chunk_size]
        if len(chunk) % 2:
            chunk = chunk[:-1]
        if chunk:
            yield chunk


def _live_connect_config(
    *,
    system_instruction: str | None,
    voice: str | None,
    temperature: float | None,
) -> dict[str, object]:
    config: dict[str, object] = {
        "response_modalities": ["AUDIO"],
        "input_audio_transcription": {},
        "output_audio_transcription": {},
    }
    if system_instruction:
        config["system_instruction"] = system_instruction
    if voice:
        config["speech_config"] = {
            "voice_config": {"prebuilt_voice_config": {"voice_name": voice}},
        }
    if temperature is not None:
        config["generation_config"] = {"temperature": temperature}
    return config


def _extract_realtime_events(response: object) -> list[GoogleLiveRealtimeEvent]:
    events: list[GoogleLiveRealtimeEvent] = []
    input_text = _text_from_attr_path(response, ("server_content", "input_transcription", "text"))
    if input_text:
        events.append(GoogleLiveRealtimeEvent(input_text=input_text))
    output_text = _text_from_attr_path(
        response,
        ("server_content", "output_transcription", "text"),
    )
    if output_text:
        events.append(GoogleLiveRealtimeEvent(output_text=output_text))
    data = getattr(response, "data", None)
    if isinstance(data, bytes):
        events.append(GoogleLiveRealtimeEvent(audio=data))
    server_content = getattr(response, "server_content", None)
    model_turn = getattr(server_content, "model_turn", None)
    parts = getattr(model_turn, "parts", None)
    if isinstance(parts, list | tuple):
        for part in parts:
            inline_data = getattr(part, "inline_data", None)
            inline_bytes = getattr(inline_data, "data", None)
            if isinstance(inline_bytes, bytes):
                events.append(GoogleLiveRealtimeEvent(audio=inline_bytes))
    return events


def _text_from_attr_path(source: object, path: tuple[str, ...]) -> str | None:
    value: object = source
    for attr in path:
        value = getattr(value, attr, None)
        if value is None:
            return None
    return value if isinstance(value, str) and value.strip() else None


def _normalize_transcript_text(text: str) -> str:
    return " ".join(text.split())


def _elapsed_ms(start: float | None, end: float | None) -> float | None:
    if start is None or end is None:
        return None
    return (end - start) * 1000.0


def _load_google_genai() -> tuple[Any, Any]:
    try:
        genai = importlib.import_module("google.genai")
        types = importlib.import_module("google.genai.types")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Google Live API requires the google-genai package. "
            "Install project dependencies with `python -m pip install -e '.[dev]'`."
        ) from exc
    return genai, types


def _required_config_string(value: str | None, name: str) -> str:
    clean = _optional_config_string(value)
    if clean is None:
        raise ValueError(f"{name} must not be blank.")
    return clean


def _optional_config_string(value: str | None) -> str | None:
    if value is None:
        return None
    clean = value.strip()
    return clean or None
