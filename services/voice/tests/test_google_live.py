from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from typing import Any

import pytest

from voice_agent.media.session import HandoffTarget
from voice_agent.providers.google_live import (
    GoogleLiveMediaSessionRunner,
    GoogleLiveRealtimeEvent,
    _live_connect_config,
    convert_pcm16_sample_rate,
)
from voice_agent.telephony.plivo import PlivoAudioFormat


class _CapturingTransport:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send_json(self, message: dict[str, Any]) -> None:
        self.messages.append(message)


class _ScriptedRealtimeClient:
    def __init__(self) -> None:
        self.started = False
        self.closed = False
        self.started_event = asyncio.Event()
        self.system_instruction: str | None = None
        self.texts: list[str] = []
        self.turn_texts: list[str] = []
        self.audio_inputs: list[bytes] = []
        self._events: list[GoogleLiveRealtimeEvent] = []

    async def start(self, *, system_instruction: str | None = None) -> None:
        self.started = True
        self.started_event.set()
        self.system_instruction = system_instruction

    async def send_text(self, text: str) -> None:
        self.texts.append(text)

    async def send_turn_text(self, text: str) -> None:
        self.turn_texts.append(text)

    async def accept_audio(self, audio: bytes) -> None:
        self.audio_inputs.append(audio)

    async def end_audio_stream(self) -> None:
        return None

    def queue_output(self, audio: bytes) -> None:
        self._events.append(GoogleLiveRealtimeEvent(audio=audio))

    def queue_transcript(self, *, role: str, text: str) -> None:
        if role == "user":
            self._events.append(GoogleLiveRealtimeEvent(input_text=text))
            return
        self._events.append(GoogleLiveRealtimeEvent(output_text=text))

    async def events(self) -> AsyncIterator[GoogleLiveRealtimeEvent]:
        while self._events:
            yield self._events.pop(0)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.anyio
async def test_google_live_runner_forwards_audio_and_plays_model_audio() -> None:
    transport = _CapturingTransport()
    client = _ScriptedRealtimeClient()
    client.queue_output(b"\x01\x00" * 320)
    runner = GoogleLiveMediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        transport=transport,
        client=client,
        audio_format=PlivoAudioFormat.L16_16KHZ,
        system_instruction="You are Acme Dental.",
        greeting="Hi, this is Acme.",
        google_output_sample_rate_hz=16000,
        max_play_audio_bytes=6400,
    )

    await runner.start()
    await runner.greet()
    await runner.accept_audio(b"\x02\x00" * 160)
    await runner.close()

    assert client.started
    assert client.system_instruction == "You are Acme Dental."
    assert client.texts == []
    assert client.turn_texts == []
    assert client.audio_inputs == [b"\x02\x00" * 160]
    assert transport.messages
    assert transport.messages[0]["event"] == "playAudio"
    assert transport.messages[0]["media"]["sampleRate"] == 16000


@pytest.mark.anyio
async def test_google_live_runner_switches_to_matching_handoff_client() -> None:
    transport = _CapturingTransport()
    primary_client = _ScriptedRealtimeClient()
    primary_client.queue_transcript(role="user", text="I need an appointment.")
    primary_client.queue_output(b"\x01\x00" * 320)
    target_client = _ScriptedRealtimeClient()
    target_client.queue_output(b"\x02\x00" * 320)
    factory_calls: list[tuple[str, str]] = []

    def build_handoff_client(
        target: HandoffTarget,
        conversation_context: str,
    ) -> tuple[_ScriptedRealtimeClient, str]:
        factory_calls.append((target.target_id, conversation_context))
        return target_client, f"Target prompt for {target.name}\n{conversation_context}"

    runner = GoogleLiveMediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        transport=transport,
        client=primary_client,
        audio_format=PlivoAudioFormat.L16_16KHZ,
        google_output_sample_rate_hz=16000,
        max_play_audio_bytes=6400,
        handoff_targets=[
            HandoffTarget(
                target_id="appointments",
                name="Appointment Manager",
                condition="Appointment booking, rescheduling, or cancellation",
                instruction="Handle scheduling details.",
            )
        ],
        handoff_client_factory=build_handoff_client,
    )

    await runner.start()
    await runner.accept_audio(b"\x02\x00" * 160)
    await asyncio.wait_for(target_client.started_event.wait(), timeout=0.5)
    await asyncio.sleep(0)
    await runner.close()

    assert primary_client.closed
    assert target_client.started
    assert target_client.system_instruction is not None
    assert "Target prompt for Appointment Manager" in target_client.system_instruction
    assert target_client.turn_texts == [
        "Continue the live phone call as Appointment Manager. "
        "The latest caller request is: I need an appointment."
    ]
    assert target_client.texts == []
    assert factory_calls
    assert factory_calls[0][0] == "appointments"
    assert "user: I need an appointment." in factory_calls[0][1]
    assert len(transport.messages) == 1
    assert transport.messages[0]["event"] == "playAudio"
    assert base64.b64decode(transport.messages[0]["media"]["payload"]) == b"\x02\x00" * 320


@pytest.mark.anyio
async def test_google_live_runner_records_transcripts_and_latency() -> None:
    transport = _CapturingTransport()
    client = _ScriptedRealtimeClient()
    client.queue_transcript(role="user", text="I need an appointment.")
    client.queue_output(b"\x01\x00" * 320)
    client.queue_transcript(role="assistant", text="I can help book that.")
    transcript_updates: list[str] = []
    latency_events: list[Any] = []
    runner = GoogleLiveMediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        transport=transport,
        client=client,
        audio_format=PlivoAudioFormat.L16_16KHZ,
        google_output_sample_rate_hz=16000,
        transcript_observer=transcript_updates.append,
        latency_observer=latency_events.append,
        max_play_audio_bytes=6400,
    )

    await runner.start()
    await runner.accept_audio(b"\x02\x00" * 160)
    await runner.close()

    assert transcript_updates
    assert '"role": "user"' in transcript_updates[-1]
    assert "I need an appointment." in transcript_updates[-1]
    assert '"role": "assistant"' in transcript_updates[-1]
    assert "I can help book that." in transcript_updates[-1]
    assert latency_events
    snapshot = latency_events[-1]
    assert snapshot.call_id == "call-1"
    assert snapshot.stream_id == "stream-1"
    assert snapshot.turn_commit_source == "google_live_input_transcription"
    assert snapshot.transcript_chars == len("I need an appointment.")
    assert snapshot.turn_committed_to_first_play_audio_ms is not None
    assert snapshot.first_audio_to_first_play_audio_ms is not None


@pytest.mark.anyio
async def test_google_live_latency_never_reports_negative_post_speech_commit() -> None:
    transport = _CapturingTransport()
    client = _ScriptedRealtimeClient()
    latency_events: list[Any] = []
    runner = GoogleLiveMediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        transport=transport,
        client=client,
        audio_format=PlivoAudioFormat.L16_16KHZ,
        google_output_sample_rate_hz=16000,
        latency_observer=latency_events.append,
    )
    runner._current_turn_epoch = 1
    runner._current_turn_committed_at = 10.0
    runner._last_voice_frame_at = 12.0
    runner._first_google_audio_at = 14.0
    runner._first_play_audio_at = 15.0

    runner._emit_latency_if_ready()

    assert latency_events
    assert latency_events[0].last_voice_frame_to_turn_committed_ms == 0.0
    assert latency_events[0].turn_committed_to_first_play_audio_ms == 3000.0


@pytest.mark.anyio
async def test_google_live_runner_merges_transcript_chunks_into_turns() -> None:
    transport = _CapturingTransport()
    client = _ScriptedRealtimeClient()
    client.queue_transcript(role="user", text=" I")
    client.queue_transcript(role="user", text=" nee")
    client.queue_transcript(role="user", text="d to")
    client.queue_transcript(role="user", text=" book")
    client.queue_transcript(role="user", text=" a")
    client.queue_transcript(role="user", text=" den")
    client.queue_transcript(role="user", text="tal appointment.")
    client.queue_output(b"\x01\x00" * 320)
    client.queue_transcript(role="assistant", text=" I can")
    client.queue_transcript(role="assistant", text=" help.")
    transcript_updates: list[str] = []
    runner = GoogleLiveMediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        transport=transport,
        client=client,
        audio_format=PlivoAudioFormat.L16_16KHZ,
        google_output_sample_rate_hz=16000,
        transcript_observer=transcript_updates.append,
        max_play_audio_bytes=6400,
    )

    await runner.start()
    await runner.accept_audio(b"\x02\x00" * 160)
    await runner.close()

    assert transcript_updates[-1] == (
        '[{"role": "user", "text": "I need to book a dental appointment.", "turn": 1}, '
        '{"role": "assistant", "text": "I can help.", "turn": 1}]'
    )


def test_google_live_connect_config_enables_audio_transcriptions() -> None:
    config = _live_connect_config(
        system_instruction="You are Acme.",
        voice="Aoede",
        temperature=0.4,
    )

    assert config["response_modalities"] == ["AUDIO"]
    assert config["input_audio_transcription"] == {}
    assert config["output_audio_transcription"] == {}


def test_convert_pcm16_sample_rate_downsamples_google_24khz_to_plivo_16khz() -> None:
    input_audio = b"".join(sample.to_bytes(2, "little", signed=True) for sample in range(6))

    output_audio = convert_pcm16_sample_rate(input_audio, from_hz=24000, to_hz=16000)

    assert len(output_audio) == 8
