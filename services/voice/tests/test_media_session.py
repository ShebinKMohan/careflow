import asyncio
import base64
import json
import struct
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

import pytest

from voice_agent.media.session import (
    HandoffTarget,
    MediaSessionRunner,
    VoiceActionRequest,
    VoiceTurnLatencySnapshot,
)
from voice_agent.providers.base import TranscriptEvent
from voice_agent.providers.local import LocalLLMClient, LocalSpeechToText, LocalTextToSpeech
from voice_agent.telephony.plivo import PlivoAudioFormat


def _active_pcm_frame(num_samples: int = 320, amplitude: int = 6000) -> bytes:
    """A PCM16 frame loud enough to count as caller speech for barge-in."""
    return b"".join(
        struct.pack("<h", amplitude if i % 2 == 0 else -amplitude) for i in range(num_samples)
    )


class CapturingTransport:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.message_sent = asyncio.Event()

    async def send_json(self, message: dict[str, Any]) -> None:
        self.messages.append(message)
        self.message_sent.set()


@pytest.mark.anyio
async def test_media_runner_sends_generated_audio_back_to_plivo() -> None:
    transport = CapturingTransport()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=LocalSpeechToText(trigger_phrase="hello"),
        llm=LocalLLMClient(response_text="Thanks, I can help with that."),
        tts=LocalTextToSpeech(),
        transport=transport,
    )

    await runner.accept_audio(b"hello")
    await runner.close()

    assert transport.messages
    assert transport.messages[0]["event"] == "playAudio"
    assert transport.messages[0]["media"]["contentType"] == "audio/x-l16"
    assert transport.messages[0]["media"]["sampleRate"] == 16000
    assert base64.b64decode(transport.messages[0]["media"]["payload"])


@pytest.mark.anyio
async def test_media_runner_emits_structured_transcript_updates() -> None:
    updates: list[str] = []
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=LocalSpeechToText(trigger_phrase="hello"),
        llm=LocalLLMClient(response_text="I can help with that."),
        tts=LocalTextToSpeech(),
        transport=CapturingTransport(),
        transcript_observer=updates.append,
    )

    await runner.accept_audio(b"hello")
    await runner.close()

    transcript = json.loads(updates[-1])
    assert transcript == [
        {"role": "user", "text": "hello", "turn": 1},
        {"role": "assistant", "text": "I can help with that.", "turn": 1},
    ]


@pytest.mark.anyio
async def test_media_runner_switches_to_matching_handoff_target_before_reply() -> None:
    primary_llm = RecordingLLMClient(response_text="Front desk response.")
    appointment_llm = RecordingLLMClient(response_text="Appointment specialist response.")
    factory_calls: list[tuple[str, str]] = []

    def build_handoff_llm(target: HandoffTarget, conversation_context: str) -> RecordingLLMClient:
        factory_calls.append((target.target_id, conversation_context))
        return appointment_llm

    transport = CapturingTransport()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="I need to book an appointment tomorrow"),
        llm=primary_llm,
        tts=TextEncodingSpeech(),
        transport=transport,
        handoff_targets=[
            HandoffTarget(
                target_id="appointments",
                name="Appointment Manager",
                condition="Appointment booking, rescheduling, or cancellation",
                instruction="Handle scheduling details.",
            )
        ],
        handoff_llm_factory=build_handoff_llm,
    )

    await runner.accept_audio(b"caller audio")
    await runner.close()

    assert primary_llm.user_texts == []
    assert appointment_llm.user_texts == ["I need to book an appointment tomorrow"]
    assert factory_calls
    assert factory_calls[0][0] == "appointments"
    assert "I need to book an appointment tomorrow" in factory_calls[0][1]
    assert _decode_text_audio_payload(transport.messages[0]) == b"Appointment specialist response."


@pytest.mark.anyio
async def test_media_runner_speaks_greeting_on_greet() -> None:
    transport = CapturingTransport()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=LocalSpeechToText(trigger_phrase="hello"),
        llm=NeverReplyingLLMClient(),
        tts=StaticAudioTextToSpeech(b"\x01\x00\x02\x00"),
        transport=transport,
        greeting="Hello, welcome to Acme Dental.",
    )

    await runner.greet()
    assert runner._turn_task is not None
    await runner._turn_task
    await runner.close()

    # The greeting plays via TTS only (the LLM that never replies is never called).
    assert [message["event"] for message in transport.messages] == ["playAudio"]
    assert base64.b64decode(transport.messages[0]["media"]["payload"]) == b"\x01\x00\x02\x00"


@pytest.mark.anyio
async def test_media_runner_greets_only_once() -> None:
    transport = CapturingTransport()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=LocalSpeechToText(trigger_phrase="hello"),
        llm=NeverReplyingLLMClient(),
        tts=StaticAudioTextToSpeech(b"\x01\x00"),
        transport=transport,
        greeting="Hi there.",
    )

    await runner.greet()
    first_task = runner._turn_task
    assert first_task is not None
    await first_task
    sent_after_first = len(transport.messages)

    await runner.greet()  # already greeted -> no-op
    await runner.close()

    assert len(transport.messages) == sent_after_first


@pytest.mark.anyio
async def test_media_runner_uses_shared_audio_cache_for_greeting() -> None:
    from voice_agent.workflow.audio_cache import AudioCacheContext, InMemoryAudioCache

    cache = InMemoryAudioCache()
    context = AudioCacheContext(tts_provider="local")

    first_transport = CapturingTransport()
    first_tts = RecordingTextToSpeech()
    first_runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=LocalSpeechToText(trigger_phrase="hello"),
        llm=NeverReplyingLLMClient(),
        tts=first_tts,
        transport=first_transport,
        greeting="Hi, this is VoicePilot.",
        audio_cache=cache,
        audio_cache_context=context,
    )

    try:
        await first_runner.greet()
        assert first_runner._turn_task is not None
        await first_runner._turn_task
    finally:
        await first_runner.close()

    assert first_tts.texts == ["Hi, this is VoicePilot."]
    assert cache.set_count == 1

    second_transport = CapturingTransport()
    second_tts = RecordingTextToSpeech()
    second_runner = MediaSessionRunner(
        call_id="call-2",
        stream_id="stream-2",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=LocalSpeechToText(trigger_phrase="hello"),
        llm=NeverReplyingLLMClient(),
        tts=second_tts,
        transport=second_transport,
        greeting="Hi, this is VoicePilot.",
        audio_cache=cache,
        audio_cache_context=context,
    )

    try:
        await second_runner.greet()
        assert second_runner._turn_task is not None
        await second_runner._turn_task
    finally:
        await second_runner.close()

    assert second_tts.texts == []
    assert _decode_text_audio_payload(second_transport.messages[0]) == b"Hi, this is VoicePilot."
    assert cache.hit_count == 1


@pytest.mark.anyio
async def test_media_runner_greet_is_noop_without_greeting() -> None:
    transport = CapturingTransport()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=LocalSpeechToText(trigger_phrase="hello"),
        llm=NeverReplyingLLMClient(),
        tts=StaticAudioTextToSpeech(b"\x01\x00"),
        transport=transport,
    )

    await runner.greet()
    assert runner._turn_task is None
    await runner.close()

    assert transport.messages == []


@pytest.mark.anyio
async def test_media_runner_start_prewarm_starts_providers_and_transcript_loop() -> None:
    transport = CapturingTransport()
    stt = StartableStreamingSpeechToText()
    tts = StartableTextToSpeech()
    llm = StartableLLMClient(response_text="Started.")
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=tts,
        transport=transport,
    )

    try:
        await runner.start()
        await asyncio.wait_for(stt.started.wait(), timeout=0.5)
        await asyncio.wait_for(llm.started.wait(), timeout=0.5)
        await asyncio.wait_for(tts.started.wait(), timeout=0.5)
        await stt.emit(TranscriptEvent(text="I need service", is_final=True, speech_final=True))
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)
    finally:
        await runner.close()

    assert llm.user_texts == ["I need service"]
    assert tts.texts == ["Started."]


@pytest.mark.anyio
async def test_media_runner_records_first_audio_latency_snapshot() -> None:
    transport = CapturingTransport()
    snapshots: list[VoiceTurnLatencySnapshot] = []
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=LocalSpeechToText(trigger_phrase="hello"),
        llm=LocalLLMClient(response_text="Ready."),
        tts=StaticAudioTextToSpeech(b"\x01\x00"),
        transport=transport,
        latency_observer=snapshots.append,
    )

    await runner.start()
    await runner.accept_audio(b"hello")
    await runner.close()

    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.call_id == "call-1"
    assert snapshot.stream_id == "stream-1"
    assert snapshot.transcript_chars == len("hello")
    assert snapshot.response_plan_action == "streaming_llm"
    assert snapshot.response_plan_confidence == 1.0
    assert snapshot.response_plan_reason == "default_open_ended_path"
    assert snapshot.media_start_to_first_audio_ms is not None
    assert snapshot.first_audio_to_speech_final_ms is not None
    assert snapshot.speech_final_to_turn_committed_ms is not None
    assert snapshot.turn_committed_to_llm_first_chunk_ms is not None
    assert snapshot.llm_first_chunk_to_tts_first_audio_ms is not None
    assert snapshot.tts_first_audio_to_first_play_audio_ms is not None
    assert snapshot.turn_committed_to_first_play_audio_ms is not None
    assert snapshot.first_audio_to_first_play_audio_ms is not None
    assert all(
        value is None or value >= 0
        for value in (
            snapshot.media_start_to_first_audio_ms,
            snapshot.first_audio_to_speech_final_ms,
            snapshot.speech_final_to_turn_committed_ms,
            snapshot.turn_committed_to_llm_first_chunk_ms,
            snapshot.llm_first_chunk_to_tts_first_audio_ms,
            snapshot.tts_first_audio_to_first_play_audio_ms,
            snapshot.turn_committed_to_first_play_audio_ms,
            snapshot.first_audio_to_first_play_audio_ms,
        )
    )


@pytest.mark.anyio
async def test_media_runner_records_post_speech_latency_snapshot() -> None:
    transport = CapturingTransport()
    snapshots: list[VoiceTurnLatencySnapshot] = []
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="caller request"),
        llm=LocalLLMClient(response_text="Ready."),
        tts=StaticAudioTextToSpeech(b"\x01\x00"),
        transport=transport,
        latency_observer=snapshots.append,
    )

    await runner.accept_audio(_active_pcm_frame())
    await runner.close()

    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.first_audio_to_last_voice_frame_ms is not None
    assert snapshot.last_voice_frame_to_turn_committed_ms is not None
    assert snapshot.last_voice_frame_to_first_play_audio_ms is not None
    assert snapshot.first_audio_to_last_voice_frame_ms >= 0
    assert snapshot.last_voice_frame_to_turn_committed_ms >= 0
    assert snapshot.last_voice_frame_to_first_play_audio_ms >= 0


@pytest.mark.anyio
async def test_media_runner_latency_observer_failure_does_not_break_turn() -> None:
    transport = CapturingTransport()

    def broken_observer(snapshot: VoiceTurnLatencySnapshot) -> None:
        del snapshot
        raise RuntimeError("metrics sink down")

    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=LocalSpeechToText(trigger_phrase="hello"),
        llm=LocalLLMClient(response_text="Ready."),
        tts=StaticAudioTextToSpeech(b"\x01\x00"),
        transport=transport,
        latency_observer=broken_observer,
    )

    await runner.accept_audio(b"hello")
    await runner.close()

    assert [message["event"] for message in transport.messages] == ["playAudio"]


@pytest.mark.anyio
async def test_media_runner_splits_large_tts_audio_chunks_for_plivo() -> None:
    transport = CapturingTransport()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="caller request"),
        llm=LocalLLMClient(response_text="reply"),
        tts=StaticAudioTextToSpeech(b"\x01\x00\x02\x00\x03\x00"),
        transport=transport,
        max_play_audio_bytes=4,
    )

    await runner.accept_audio(b"caller audio")
    await runner.close()

    assert [message["event"] for message in transport.messages] == [
        "playAudio",
        "playAudio",
    ]
    assert [base64.b64decode(message["media"]["payload"]) for message in transport.messages] == [
        b"\x01\x00\x02\x00",
        b"\x03\x00",
    ]


def test_media_runner_rejects_misaligned_max_play_audio_bytes() -> None:
    with pytest.raises(ValueError, match="max_play_audio_bytes must be an even integer"):
        MediaSessionRunner(
            call_id="call-1",
            stream_id="stream-1",
            audio_format=PlivoAudioFormat.L16_16KHZ,
            stt=LocalSpeechToText(trigger_phrase="hello"),
            llm=LocalLLMClient(response_text="reply"),
            tts=LocalTextToSpeech(),
            transport=CapturingTransport(),
            max_play_audio_bytes=3,
        )


def test_media_runner_rejects_overlong_pre_response_ack_text() -> None:
    with pytest.raises(ValueError, match="pre_response_ack_text must be at most 90"):
        MediaSessionRunner(
            call_id="call-1",
            stream_id="stream-1",
            audio_format=PlivoAudioFormat.L16_16KHZ,
            stt=LocalSpeechToText(trigger_phrase="hello"),
            llm=LocalLLMClient(response_text="reply"),
            tts=LocalTextToSpeech(),
            transport=CapturingTransport(),
            pre_response_ack_text="x" * 91,
        )


@pytest.mark.anyio
async def test_media_runner_clears_buffer_when_caller_interrupts_playback() -> None:
    transport = CapturingTransport()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=LocalSpeechToText(trigger_phrase="interrupt"),
        llm=LocalLLMClient(response_text="A long response."),
        tts=LocalTextToSpeech(),
        transport=transport,
    )

    await runner.accept_audio(b"interrupt")
    await runner.accept_audio(b"interrupt again")
    await runner.close()

    events = [message["event"] for message in transport.messages]
    assert "clearAudio" in events


@pytest.mark.anyio
async def test_media_runner_processes_transcript_that_arrives_after_audio_frame() -> None:
    transport = CapturingTransport()
    stt = DelayedSpeechToText()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=LocalLLMClient(response_text="Delayed transcript handled."),
        tts=LocalTextToSpeech(),
        transport=transport,
    )

    await runner.accept_audio(b"raw caller audio")
    await stt.emit_final("I need service")
    await runner.close()

    assert transport.messages
    assert transport.messages[0]["event"] == "playAudio"


@pytest.mark.anyio
async def test_media_runner_bounds_hanging_stt_audio_ingest() -> None:
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=NeverAcceptingSpeechToText(),
        llm=LocalLLMClient(response_text="unused"),
        tts=LocalTextToSpeech(),
        transport=CapturingTransport(),
        audio_ingest_timeout_seconds=0.01,
    )

    await asyncio.wait_for(runner.accept_audio(b"raw caller audio"), timeout=0.2)
    await runner.close()


@pytest.mark.anyio
async def test_media_runner_recovers_after_transient_stt_audio_ingest_failure() -> None:
    transport = CapturingTransport()
    stt = FailingOnceSpeechToText()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=LocalLLMClient(response_text="Recovered."),
        tts=LocalTextToSpeech(),
        transport=transport,
    )

    await asyncio.wait_for(runner.accept_audio(b"first frame"), timeout=0.2)
    await runner.accept_audio(b"second frame")
    await runner.close()

    assert stt.accepted_audio == [b"first frame", b"second frame"]
    assert [message["event"] for message in transport.messages] == ["playAudio"]


@pytest.mark.anyio
async def test_media_runner_bounds_hanging_plivo_play_audio_send() -> None:
    transport = HangingTransport()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=LocalSpeechToText(trigger_phrase="hello"),
        llm=LocalLLMClient(response_text="This response should time out on send."),
        tts=LocalTextToSpeech(),
        transport=transport,
        turn_timeout_seconds=10,
        plivo_send_timeout_seconds=0.01,
    )

    await asyncio.wait_for(runner.accept_audio(b"hello"), timeout=0.2)
    await runner.close()

    assert transport.attempted_events == ["playAudio"]


@pytest.mark.anyio
async def test_media_runner_bounds_hanging_clear_audio_send_on_barge_in() -> None:
    transport = HangingClearAudioTransport()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=LocalSpeechToText(trigger_phrase="interrupt"),
        llm=LocalLLMClient(response_text="A response."),
        tts=LocalTextToSpeech(),
        transport=transport,
        turn_timeout_seconds=10,
        plivo_send_timeout_seconds=0.01,
    )

    await runner.accept_audio(b"interrupt")
    await asyncio.wait_for(runner.accept_audio(b"interrupt again"), timeout=0.2)
    await runner.close()

    assert transport.attempted_events == ["playAudio", "clearAudio", "playAudio"]


@pytest.mark.anyio
async def test_media_runner_stops_streaming_tts_after_caller_barge_in() -> None:
    transport = CapturingTransport()
    stt = DelayedSpeechToText()
    tts = ControlledChunkedTextToSpeech()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=LocalLLMClient(response_text="This response streams in multiple chunks."),
        tts=tts,
        transport=transport,
    )

    await runner.accept_audio(b"raw caller audio")
    await stt.emit_final("I need service")
    await tts.first_chunk_sent.wait()

    # Sustained caller speech (>= barge_in_min_frames) triggers barge-in.
    await runner.accept_audio(_active_pcm_frame())
    await runner.accept_audio(_active_pcm_frame())
    tts.release_second_chunk.set()
    await runner.close()

    events = [message["event"] for message in transport.messages]
    assert events == ["playAudio", "clearAudio"]


@pytest.mark.anyio
async def test_media_runner_emits_latency_when_barge_in_cancels_after_answer_audio() -> None:
    transport = CapturingTransport()
    stt = DelayedSpeechToText()
    llm = GatedSecondChunkLLMClient(
        first_chunk="I can help with that, ",
        second_chunk="let me check.",
    )
    snapshots: list[VoiceTurnLatencySnapshot] = []
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=StaticAudioTextToSpeech(b"\x01\x00" * 16000),
        transport=transport,
        latency_observer=snapshots.append,
    )

    try:
        await runner.accept_audio(b"raw caller audio")
        await stt.emit_final("I need service")
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)

        # Sustained caller speech barges in before the LLM stream can complete.
        await runner.accept_audio(_active_pcm_frame())
        await runner.accept_audio(_active_pcm_frame())
    finally:
        await runner.close()

    events = [message["event"] for message in transport.messages]
    assert "playAudio" in events
    assert "clearAudio" in events
    assert len(snapshots) == 1
    assert snapshots[0].turn_committed_to_answer_first_play_audio_ms is not None
    assert snapshots[0].answer_first_play_audio_to_llm_stream_completed_ms is None


@pytest.mark.anyio
async def test_media_runner_does_not_clear_audio_after_playback_has_drained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 0.0
    monkeypatch.setattr("voice_agent.media.session.time.perf_counter", lambda: now)
    transport = CapturingTransport()
    stt = DelayedSpeechToText()
    llm = RecordingLLMClient(response_text="Done.")
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=StaticAudioTextToSpeech(b"\x01\x00" * 160),
        transport=transport,
        barge_in_min_frames=2,
    )

    try:
        await runner.start()
        await runner.accept_audio(_active_pcm_frame())
        await stt.emit_final("first request")
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)

        now = 1.0
        await runner.accept_audio(_active_pcm_frame())
        await runner.accept_audio(_active_pcm_frame())
        await stt.emit_final("second request")
        await asyncio.wait_for(_wait_for_messages(transport, count=2), timeout=0.5)
    finally:
        await runner.close()

    assert llm.user_texts == ["first request", "second request"]
    assert [message["event"] for message in transport.messages] == [
        "playAudio",
        "playAudio",
    ]


@pytest.mark.anyio
async def test_media_runner_times_out_slow_turn_generation() -> None:
    transport = CapturingTransport()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=LocalSpeechToText(trigger_phrase="hello"),
        llm=NeverReplyingLLMClient(),
        tts=LocalTextToSpeech(),
        transport=transport,
        turn_timeout_seconds=0.01,
    )

    await asyncio.wait_for(runner.accept_audio(b"hello"), timeout=0.2)
    await runner.close()

    assert [message["event"] for message in transport.messages] == ["playAudio"]


@pytest.mark.anyio
async def test_media_runner_speaks_fallback_when_turn_times_out_before_playback() -> None:
    transport = CapturingTransport()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=LocalSpeechToText(trigger_phrase="hello"),
        llm=NeverReplyingLLMClient(),
        tts=TextEncodingSpeech(),
        transport=transport,
        turn_timeout_seconds=0.01,
    )

    await asyncio.wait_for(runner.accept_audio(b"hello"), timeout=0.2)
    await runner.close()

    assert [message["event"] for message in transport.messages] == ["playAudio"]
    assert _decode_text_audio_payload(transport.messages[0]) == (
        b"I'm sorry, I had trouble processing that. Please try again."
    )


@pytest.mark.anyio
async def test_media_runner_contains_llm_turn_failure() -> None:
    transport = CapturingTransport()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=LocalSpeechToText(trigger_phrase="hello"),
        llm=FailingLLMClient(),
        tts=LocalTextToSpeech(),
        transport=transport,
    )

    await runner.accept_audio(b"hello")
    await runner.close()

    assert [message["event"] for message in transport.messages] == ["playAudio"]


@pytest.mark.anyio
async def test_media_runner_speaks_fallback_when_llm_fails_before_playback() -> None:
    transport = CapturingTransport()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=LocalSpeechToText(trigger_phrase="hello"),
        llm=FailingLLMClient(),
        tts=TextEncodingSpeech(),
        transport=transport,
    )

    await runner.accept_audio(b"hello")
    await runner.close()

    assert [message["event"] for message in transport.messages] == ["playAudio"]
    assert _decode_text_audio_payload(transport.messages[0]) == (
        b"I'm sorry, I had trouble processing that. Please try again."
    )


@pytest.mark.anyio
async def test_media_runner_rejects_oversized_transcript_without_calling_llm() -> None:
    transport = CapturingTransport()
    llm = RecordingLLMClient(response_text="This response should not be generated.")
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="x" * 9),
        llm=llm,
        tts=TextEncodingSpeech(),
        transport=transport,
        max_transcript_chars=8,
    )

    await runner.accept_audio(b"caller audio")
    await runner.close()

    assert llm.user_texts == []
    assert [message["event"] for message in transport.messages] == ["playAudio"]
    assert _decode_text_audio_payload(transport.messages[0]) == (
        b"I'm sorry, I had trouble processing that. Please try again."
    )


@pytest.mark.anyio
async def test_media_runner_rejects_oversized_llm_reply_before_tts() -> None:
    transport = CapturingTransport()
    llm = RecordingLLMClient(response_text="x" * 9)
    tts = RecordingTextToSpeech()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="caller request"),
        llm=llm,
        tts=tts,
        transport=transport,
        max_reply_chars=8,
    )

    await runner.accept_audio(b"caller audio")
    await runner.close()

    assert llm.user_texts == ["caller request"]
    assert tts.texts == ["I'm sorry, I had trouble processing that. Please try again."]
    assert [message["event"] for message in transport.messages] == ["playAudio"]
    assert _decode_text_audio_payload(transport.messages[0]) == (
        b"I'm sorry, I had trouble processing that. Please try again."
    )


@pytest.mark.anyio
async def test_media_runner_stops_reading_llm_stream_when_reply_limit_is_exceeded() -> None:
    transport = CapturingTransport()
    llm = ChunkedRecordingLLMClient(chunks=["abcd", "efgh", "i", "extra"])
    tts = RecordingTextToSpeech()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="caller request"),
        llm=llm,
        tts=tts,
        transport=transport,
        max_reply_chars=8,
    )

    await runner.accept_audio(b"caller audio")
    await runner.close()

    assert llm.yielded_chunks == ["abcd", "efgh", "i"]
    assert tts.texts == ["I'm sorry, I had trouble processing that. Please try again."]
    assert [message["event"] for message in transport.messages] == ["playAudio"]


@pytest.mark.anyio
async def test_media_runner_bounds_hanging_fallback_tts() -> None:
    transport = CapturingTransport()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=LocalSpeechToText(trigger_phrase="hello"),
        llm=FailingLLMClient(),
        tts=NeverSynthesizingTextToSpeech(),
        transport=transport,
        fallback_timeout_seconds=0.01,
    )

    await asyncio.wait_for(runner.accept_audio(b"hello"), timeout=0.2)
    await runner.close()

    assert transport.messages == []


@pytest.mark.anyio
async def test_media_runner_clears_buffer_after_tts_turn_failure() -> None:
    transport = CapturingTransport()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=LocalSpeechToText(trigger_phrase="hello"),
        llm=LocalLLMClient(response_text="partial response"),
        tts=FailingAfterFirstChunkTextToSpeech(),
        transport=transport,
    )

    await runner.accept_audio(b"hello")
    await runner.close()

    events = [message["event"] for message in transport.messages]
    assert events == ["playAudio", "clearAudio"]


@pytest.mark.anyio
async def test_media_runner_closes_all_per_call_providers() -> None:
    llm = ClosableLLMClient()
    tts = ClosableTextToSpeech()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=LocalSpeechToText(trigger_phrase="hello"),
        llm=llm,
        tts=tts,
        transport=CapturingTransport(),
    )

    await runner.close()

    assert llm.closed
    assert tts.closed


@pytest.mark.anyio
async def test_media_runner_bounds_hanging_provider_close() -> None:
    stt = NeverClosingSpeechToText()
    llm = NeverClosingLLMClient()
    tts = NeverClosingTextToSpeech()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=tts,
        transport=CapturingTransport(),
        provider_close_timeout_seconds=0.01,
    )

    await asyncio.wait_for(runner.close(), timeout=0.2)

    assert stt.close_started
    assert llm.close_started
    assert tts.close_started


@pytest.mark.anyio
async def test_media_runner_close_cancels_inflight_turn_promptly() -> None:
    stt = DelayedSpeechToText()
    tts = ControlledChunkedTextToSpeech()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=LocalLLMClient(response_text="This response is still streaming."),
        tts=tts,
        transport=CapturingTransport(),
        turn_timeout_seconds=10,
    )

    await runner.accept_audio(b"raw caller audio")
    await stt.emit_final("I need help")
    await tts.first_chunk_sent.wait()

    try:
        await asyncio.wait_for(runner.close(), timeout=0.2)
    finally:
        tts.release_second_chunk.set()


@pytest.mark.anyio
async def test_media_runner_close_bounds_turn_task_that_ignores_cancellation() -> None:
    stt = DelayedSpeechToText()
    tts = CancellationIgnoringTextToSpeech()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=LocalLLMClient(response_text="This response is still streaming."),
        tts=tts,
        transport=CapturingTransport(),
        turn_timeout_seconds=10,
        provider_close_timeout_seconds=0.01,
    )

    await runner.accept_audio(b"raw caller audio")
    await stt.emit_final("I need help")
    await tts.first_chunk_sent.wait()

    await asyncio.wait_for(runner.close(), timeout=0.2)
    tts.release.set()
    await asyncio.wait_for(tts.finished.wait(), timeout=0.2)

    assert tts.cancel_seen


@pytest.mark.anyio
async def test_media_runner_cancels_stale_turn_when_new_final_transcript_arrives() -> None:
    transport = CapturingTransport()
    stt = DelayedSpeechToText()
    llm = LatestTurnLLMClient()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=TextEncodingSpeech(),
        transport=transport,
        turn_timeout_seconds=10,
    )

    try:
        await runner.accept_audio(b"raw caller audio")
        await stt.emit_final("first request")
        await asyncio.wait_for(llm.first_started.wait(), timeout=0.2)
        await stt.emit_final("second request")
        await asyncio.wait_for(llm.first_cancelled.wait(), timeout=0.2)
        await asyncio.wait_for(llm.second_started.wait(), timeout=0.2)
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.2)
    finally:
        await runner.close()

    assert len(transport.messages) == 1
    assert _decode_text_audio_payload(transport.messages[0]) == b"second response"


async def _wait_for_messages(transport: CapturingTransport, *, count: int) -> None:
    while len(transport.messages) < count:
        transport.message_sent.clear()
        if len(transport.messages) >= count:
            return
        await transport.message_sent.wait()


async def _wait_for_tts_texts(tts: Any, *, count: int) -> None:
    while len(tts.texts) < count:
        tts.text_added.clear()
        if len(tts.texts) >= count:
            return
        await tts.text_added.wait()


def _decode_text_audio_payload(message: dict[str, Any]) -> bytes:
    audio = base64.b64decode(message["media"]["payload"])
    return audio.rstrip(b"\x00")


def _text_audio(text: str) -> bytes:
    audio = text.encode("utf-8")
    if len(audio) % 2:
        audio += b"\x00"
    return audio


class DelayedSpeechToText:
    def __init__(self) -> None:
        self._events: asyncio.Queue[TranscriptEvent | None] = asyncio.Queue()

    async def accept_audio(self, audio: bytes) -> None:
        del audio

    async def emit_final(self, text: str) -> None:
        await self._events.put(
            TranscriptEvent(text=text, is_final=True, confidence=0.98, speech_final=True)
        )

    async def transcripts(self) -> AsyncIterator[TranscriptEvent]:
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event

    async def close(self) -> None:
        await self._events.put(None)


class ControlledChunkedTextToSpeech:
    def __init__(self) -> None:
        self.first_chunk_sent = asyncio.Event()
        self.release_second_chunk = asyncio.Event()

    async def synthesize(
        self,
        *,
        text: str,
        audio_format: PlivoAudioFormat,
    ) -> AsyncIterator[bytes]:
        del text, audio_format
        yield b"\x01\x00"
        self.first_chunk_sent.set()
        await self.release_second_chunk.wait()
        yield b"\x02\x00"

    async def close(self) -> None:
        return None


class NeverReplyingLLMClient:
    async def generate_reply(self, *, call_id: str, user_text: str) -> AsyncIterator[str]:
        del call_id, user_text
        await asyncio.Event().wait()
        yield ""

    async def close(self) -> None:
        return None


class HangingFirstCallThenFastLLMClient:
    def __init__(self, *, response_text: str) -> None:
        self._response_text = response_text
        self.user_texts: list[str] = []
        self.first_started = asyncio.Event()
        self.first_cancelled = asyncio.Event()

    async def generate_reply(self, *, call_id: str, user_text: str) -> AsyncIterator[str]:
        del call_id
        self.user_texts.append(user_text)
        if len(self.user_texts) == 1:
            self.first_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.first_cancelled.set()
                raise
            yield ""
            return
        yield self._response_text

    async def close(self) -> None:
        return None


class FirstCallOnlyReleasableLLMClient:
    def __init__(self, *, response_text: str) -> None:
        self._response_text = response_text
        self.user_texts: list[str] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def generate_reply(self, *, call_id: str, user_text: str) -> AsyncIterator[str]:
        del call_id
        self.user_texts.append(user_text)
        if len(self.user_texts) > 1:
            raise AssertionError("speculative response should be promoted, not duplicated")
        self.started.set()
        await self.release.wait()
        yield self._response_text

    async def close(self) -> None:
        return None


class NeverAcceptingSpeechToText:
    async def accept_audio(self, audio: bytes) -> None:
        del audio
        await asyncio.Event().wait()

    async def transcripts(self) -> AsyncIterator[TranscriptEvent]:
        return
        yield TranscriptEvent(text="", is_final=False)

    async def close(self) -> None:
        return None


class FailingOnceSpeechToText:
    def __init__(self) -> None:
        self.accepted_audio: list[bytes] = []

    async def accept_audio(self, audio: bytes) -> TranscriptEvent | None:
        self.accepted_audio.append(audio)
        if len(self.accepted_audio) == 1:
            raise RuntimeError("transient Deepgram send failure")
        return TranscriptEvent(text="recovered request", is_final=True, confidence=0.9)

    async def transcripts(self) -> AsyncIterator[TranscriptEvent]:
        return
        yield TranscriptEvent(text="", is_final=False)

    async def close(self) -> None:
        return None


class SingleTranscriptSpeechToText:
    def __init__(self, *, text: str) -> None:
        self._text = text

    async def accept_audio(self, audio: bytes) -> TranscriptEvent:
        del audio
        return TranscriptEvent(text=self._text, is_final=True, confidence=0.9)

    async def transcripts(self) -> AsyncIterator[TranscriptEvent]:
        return
        yield TranscriptEvent(text="", is_final=False)

    async def close(self) -> None:
        return None


class NeverClosingSpeechToText:
    def __init__(self) -> None:
        self.close_started = False

    async def accept_audio(self, audio: bytes) -> None:
        del audio

    async def transcripts(self) -> AsyncIterator[TranscriptEvent]:
        return
        yield TranscriptEvent(text="", is_final=False)

    async def close(self) -> None:
        self.close_started = True
        await asyncio.Event().wait()


class HangingTransport:
    def __init__(self) -> None:
        self.attempted_events: list[str] = []

    async def send_json(self, message: dict[str, Any]) -> None:
        self.attempted_events.append(str(message["event"]))
        await asyncio.Event().wait()


class BlockingFirstPlayAudioTransport:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.first_play_audio_started = asyncio.Event()
        self.release_first_play_audio = asyncio.Event()
        self._blocked_first_play_audio = False

    async def send_json(self, message: dict[str, Any]) -> None:
        self.messages.append(message)
        if message["event"] == "playAudio" and not self._blocked_first_play_audio:
            self._blocked_first_play_audio = True
            self.first_play_audio_started.set()
            await self.release_first_play_audio.wait()


class HangingClearAudioTransport:
    def __init__(self) -> None:
        self.attempted_events: list[str] = []

    async def send_json(self, message: dict[str, Any]) -> None:
        event = str(message["event"])
        self.attempted_events.append(event)
        if event == "clearAudio":
            await asyncio.Event().wait()


class FailingLLMClient:
    async def generate_reply(self, *, call_id: str, user_text: str) -> AsyncIterator[str]:
        del call_id, user_text
        raise RuntimeError("orchestrator unavailable")
        yield ""

    async def close(self) -> None:
        return None


class NeverClosingLLMClient:
    def __init__(self) -> None:
        self.close_started = False

    async def generate_reply(self, *, call_id: str, user_text: str) -> AsyncIterator[str]:
        del call_id, user_text
        yield ""

    async def close(self) -> None:
        self.close_started = True
        await asyncio.Event().wait()


class FailingAfterFirstChunkTextToSpeech:
    async def synthesize(
        self,
        *,
        text: str,
        audio_format: PlivoAudioFormat,
    ) -> AsyncIterator[bytes]:
        del text, audio_format
        yield b"\x00\x01"
        raise RuntimeError("tts stream failed")

    async def close(self) -> None:
        return None


class NeverSynthesizingTextToSpeech:
    async def synthesize(
        self,
        *,
        text: str,
        audio_format: PlivoAudioFormat,
    ) -> AsyncIterator[bytes]:
        del text, audio_format
        await asyncio.Event().wait()
        yield b""

    async def close(self) -> None:
        return None


class CancellationIgnoringTextToSpeech:
    def __init__(self) -> None:
        self.first_chunk_sent = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()
        self.cancel_seen = False

    async def synthesize(
        self,
        *,
        text: str,
        audio_format: PlivoAudioFormat,
    ) -> AsyncIterator[bytes]:
        del text, audio_format
        try:
            yield b"\x01\x00"
            self.first_chunk_sent.set()
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancel_seen = True
        finally:
            self.finished.set()

    async def close(self) -> None:
        return None


class NeverClosingTextToSpeech:
    def __init__(self) -> None:
        self.close_started = False

    async def synthesize(
        self,
        *,
        text: str,
        audio_format: PlivoAudioFormat,
    ) -> AsyncIterator[bytes]:
        del text, audio_format
        yield b""

    async def close(self) -> None:
        self.close_started = True
        await asyncio.Event().wait()


class LatestTurnLLMClient:
    def __init__(self) -> None:
        self.first_started = asyncio.Event()
        self.first_cancelled = asyncio.Event()
        self.second_started = asyncio.Event()

    async def generate_reply(self, *, call_id: str, user_text: str) -> AsyncIterator[str]:
        del call_id
        if user_text == "first request":
            self.first_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.first_cancelled.set()
                raise
            yield ""
            return
        if user_text == "second request":
            self.second_started.set()
            yield "second response"
            return
        raise AssertionError(f"unexpected transcript: {user_text}")

    async def close(self) -> None:
        return None


class RecordingLLMClient:
    def __init__(self, *, response_text: str) -> None:
        self._response_text = response_text
        self.user_texts: list[str] = []

    async def generate_reply(self, *, call_id: str, user_text: str) -> AsyncIterator[str]:
        del call_id
        self.user_texts.append(user_text)
        yield self._response_text

    async def close(self) -> None:
        return None


class HistoryAwareRecordingLLMClient:
    def __init__(self, *, responses: dict[str, str]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, bool]] = []
        self.remembered_turns: list[tuple[str, str]] = []

    async def generate_reply(
        self,
        *,
        call_id: str,
        user_text: str,
        commit_history: bool = True,
    ) -> AsyncIterator[str]:
        del call_id
        self.calls.append((user_text, commit_history))
        response = self._responses[user_text]
        yield response
        if commit_history:
            self.remember_turn(
                call_id="call-1",
                user_text=user_text,
                assistant_text=response,
            )

    def remember_turn(self, *, call_id: str, user_text: str, assistant_text: str) -> None:
        del call_id
        self.remembered_turns.append((user_text, assistant_text))

    async def close(self) -> None:
        return None


class ChunkedRecordingLLMClient:
    def __init__(self, *, chunks: list[str]) -> None:
        self._chunks = chunks
        self.yielded_chunks: list[str] = []

    async def generate_reply(self, *, call_id: str, user_text: str) -> AsyncIterator[str]:
        del call_id, user_text
        for chunk in self._chunks:
            self.yielded_chunks.append(chunk)
            yield chunk

    async def close(self) -> None:
        return None


class ObservableChunkedLLMClient:
    def __init__(self, *, chunks: list[str]) -> None:
        self._chunks = chunks
        self.yielded_chunks: list[str] = []
        self.yielded_events = [asyncio.Event() for _chunk in chunks]

    async def generate_reply(self, *, call_id: str, user_text: str) -> AsyncIterator[str]:
        del call_id, user_text
        for index, chunk in enumerate(self._chunks):
            self.yielded_chunks.append(chunk)
            self.yielded_events[index].set()
            yield chunk

    async def close(self) -> None:
        return None


class GatedSecondChunkLLMClient:
    def __init__(self, *, first_chunk: str, second_chunk: str) -> None:
        self._first_chunk = first_chunk
        self._second_chunk = second_chunk
        self.first_chunk_yielded = asyncio.Event()
        self.release_second_chunk = asyncio.Event()

    async def generate_reply(self, *, call_id: str, user_text: str) -> AsyncIterator[str]:
        del call_id, user_text
        yield self._first_chunk
        self.first_chunk_yielded.set()
        await self.release_second_chunk.wait()
        yield self._second_chunk

    async def close(self) -> None:
        return None


class DelayedGatedSecondChunkLLMClient:
    def __init__(self, *, first_chunk: str, second_chunk: str) -> None:
        self._first_chunk = first_chunk
        self._second_chunk = second_chunk
        self.release_first_chunk = asyncio.Event()
        self.release_second_chunk = asyncio.Event()
        self.first_chunk_yielded = asyncio.Event()

    async def generate_reply(self, *, call_id: str, user_text: str) -> AsyncIterator[str]:
        del call_id, user_text
        await self.release_first_chunk.wait()
        yield self._first_chunk
        self.first_chunk_yielded.set()
        await self.release_second_chunk.wait()
        yield self._second_chunk

    async def close(self) -> None:
        return None


class DelayedFirstChunkLLMClient:
    def __init__(self, *, response_text: str) -> None:
        self._response_text = response_text
        self.release_first_chunk = asyncio.Event()
        self.first_chunk_yielded = asyncio.Event()

    async def generate_reply(self, *, call_id: str, user_text: str) -> AsyncIterator[str]:
        del call_id, user_text
        await self.release_first_chunk.wait()
        self.first_chunk_yielded.set()
        yield self._response_text

    async def close(self) -> None:
        return None


class ReleasableDelayedLLMClient:
    def __init__(self, *, response_text: str) -> None:
        self._response_text = response_text
        self.release_events: list[asyncio.Event] = []

    async def generate_reply(self, *, call_id: str, user_text: str) -> AsyncIterator[str]:
        del call_id, user_text
        release_event = asyncio.Event()
        self.release_events.append(release_event)
        await release_event.wait()
        yield self._response_text

    def release_turn(self, index: int) -> None:
        self.release_events[index].set()

    async def close(self) -> None:
        return None


class TextEncodingSpeech:
    async def synthesize(
        self,
        *,
        text: str,
        audio_format: PlivoAudioFormat,
    ) -> AsyncIterator[bytes]:
        del audio_format
        audio = text.encode("utf-8")
        if len(audio) % 2:
            audio += b"\x00"
        yield audio

    async def close(self) -> None:
        return None


class DelayedAnswerTextToSpeech:
    def __init__(self, *, answer_text: str, delay_seconds: float) -> None:
        self._answer_text = answer_text
        self._delay_seconds = delay_seconds
        self.texts: list[str] = []

    async def synthesize(
        self,
        *,
        text: str,
        audio_format: PlivoAudioFormat,
    ) -> AsyncIterator[bytes]:
        del audio_format
        self.texts.append(text)
        if text == self._answer_text:
            await asyncio.sleep(self._delay_seconds)
        audio = text.encode("utf-8")
        if len(audio) % 2:
            audio += b"\x00"
        yield audio

    async def close(self) -> None:
        return None


class DelayedBlockingFirstChunkTextToSpeech:
    def __init__(self, *, delay_seconds: float) -> None:
        self._delay_seconds = delay_seconds
        self.release = asyncio.Event()
        self.texts: list[str] = []

    async def synthesize(
        self,
        *,
        text: str,
        audio_format: PlivoAudioFormat,
    ) -> AsyncIterator[bytes]:
        del audio_format
        self.texts.append(text)
        await asyncio.sleep(self._delay_seconds)
        audio = text.encode("utf-8")
        if len(audio) % 2:
            audio += b"\x00"
        yield audio
        await self.release.wait()

    async def close(self) -> None:
        self.release.set()


class BlockingFirstSynthesisTextToSpeech:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.release_first_synthesis = asyncio.Event()

    async def synthesize(
        self,
        *,
        text: str,
        audio_format: PlivoAudioFormat,
    ) -> AsyncIterator[bytes]:
        del audio_format
        self.texts.append(text)
        yield _text_audio(text)
        if len(self.texts) == 1:
            await self.release_first_synthesis.wait()

    async def close(self) -> None:
        self.release_first_synthesis.set()


class HangingPreResponseAckTextToSpeech:
    def __init__(self, *, ack_text: str, answer_text: str) -> None:
        self._ack_text = ack_text
        self._answer_text = answer_text
        self.texts: list[str] = []
        self.ack_started = asyncio.Event()
        self.ack_cancelled = asyncio.Event()

    async def synthesize(
        self,
        *,
        text: str,
        audio_format: PlivoAudioFormat,
    ) -> AsyncIterator[bytes]:
        del audio_format
        self.texts.append(text)
        if text == self._ack_text:
            self.ack_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.ack_cancelled.set()
                raise
            yield b""
            return
        if text != self._answer_text:
            raise AssertionError(f"unexpected text: {text}")
        audio = text.encode("utf-8")
        if len(audio) % 2:
            audio += b"\x00"
        yield audio

    async def close(self) -> None:
        return None


class HangingPrewarmThenFreshAckTextToSpeech:
    def __init__(self, *, ack_text: str, answer_text: str) -> None:
        self._ack_text = ack_text
        self._answer_text = answer_text
        self.texts: list[str] = []
        self.prewarm_started = asyncio.Event()
        self.prewarm_cancelled = asyncio.Event()
        self._ack_calls = 0

    async def synthesize(
        self,
        *,
        text: str,
        audio_format: PlivoAudioFormat,
    ) -> AsyncIterator[bytes]:
        del audio_format
        self.texts.append(text)
        if text == self._ack_text:
            self._ack_calls += 1
            if self._ack_calls == 1:
                self.prewarm_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.prewarm_cancelled.set()
                    raise
                yield b""
                return
            yield _text_audio(text)
            return
        if text != self._answer_text:
            raise AssertionError(f"unexpected text: {text}")
        yield _text_audio(text)

    async def close(self) -> None:
        return None


class RecordingTextToSpeech:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.text_added = asyncio.Event()

    async def synthesize(
        self,
        *,
        text: str,
        audio_format: PlivoAudioFormat,
    ) -> AsyncIterator[bytes]:
        del audio_format
        self.texts.append(text)
        self.text_added.set()
        audio = text.encode("utf-8")
        if len(audio) % 2:
            audio += b"\x00"
        yield audio

    async def close(self) -> None:
        return None


class StartableTextToSpeech:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.texts: list[str] = []

    async def start(self) -> None:
        self.started.set()

    async def synthesize(
        self,
        *,
        text: str,
        audio_format: PlivoAudioFormat,
    ) -> AsyncIterator[bytes]:
        del audio_format
        self.texts.append(text)
        yield b"\x00\x01"

    async def close(self) -> None:
        return None


class StartableLLMClient:
    def __init__(self, *, response_text: str) -> None:
        self._response_text = response_text
        self.started = asyncio.Event()
        self.user_texts: list[str] = []

    async def start(self) -> None:
        self.started.set()

    async def generate_reply(self, *, call_id: str, user_text: str) -> AsyncIterator[str]:
        del call_id
        self.user_texts.append(user_text)
        yield self._response_text

    async def close(self) -> None:
        return None


class StaticAudioTextToSpeech:
    def __init__(self, audio: bytes) -> None:
        self._audio = audio

    async def synthesize(
        self,
        *,
        text: str,
        audio_format: PlivoAudioFormat,
    ) -> AsyncIterator[bytes]:
        del text, audio_format
        yield self._audio

    async def close(self) -> None:
        return None


class ClosableLLMClient:
    def __init__(self) -> None:
        self.closed = False

    async def generate_reply(self, *, call_id: str, user_text: str) -> AsyncIterator[str]:
        del call_id, user_text
        yield "closed"

    async def close(self) -> None:
        self.closed = True


class ClosableTextToSpeech:
    def __init__(self) -> None:
        self.closed = False

    async def synthesize(
        self,
        *,
        text: str,
        audio_format: PlivoAudioFormat,
    ) -> AsyncIterator[bytes]:
        del text, audio_format
        yield b"\x00\x01"

    async def close(self) -> None:
        self.closed = True


class ScriptedStreamingSpeechToText:
    """Streaming STT whose transcripts() yields events pushed via emit()."""

    def __init__(self) -> None:
        self._events: asyncio.Queue[TranscriptEvent | None] = asyncio.Queue()

    async def accept_audio(self, audio: bytes) -> None:
        del audio

    async def emit(self, event: TranscriptEvent) -> None:
        await self._events.put(event)

    async def transcripts(self) -> AsyncIterator[TranscriptEvent]:
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event

    async def close(self) -> None:
        await self._events.put(None)


class StartableStreamingSpeechToText(ScriptedStreamingSpeechToText):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def start(self) -> None:
        self.started.set()


@pytest.mark.anyio
async def test_media_runner_accumulates_streaming_segments_until_speech_final() -> None:
    transport = CapturingTransport()
    stt = ScriptedStreamingSpeechToText()
    llm = RecordingLLMClient(response_text="Sure.")
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=RecordingTextToSpeech(),
        transport=transport,
    )

    try:
        await runner.accept_audio(b"\x10\x00" * 200)  # starts the transcript loop
        await stt.emit(TranscriptEvent(text="I want to", is_final=True, speech_final=False))
        await stt.emit(TranscriptEvent(text="check my balance", is_final=True, speech_final=True))
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)
    finally:
        await runner.close()

    # Exactly one turn, with the full accumulated utterance (never the first segment alone).
    assert llm.user_texts == ["I want to check my balance"]


@pytest.mark.anyio
async def test_media_runner_holds_on_trailing_filler_then_commits_joined_utterance() -> None:
    transport = CapturingTransport()
    stt = ScriptedStreamingSpeechToText()
    llm = RecordingLLMClient(response_text="Okay.")
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=RecordingTextToSpeech(),
        transport=transport,
    )

    try:
        await runner.accept_audio(b"\x10\x00" * 200)
        # Caller trails off on a filler; end-of-turn signal should be HELD, not committed.
        await stt.emit(TranscriptEvent(text="umm", is_final=True, speech_final=True))
        await asyncio.sleep(0.05)
        assert llm.user_texts == []
        # Caller resumes; next end-of-turn commits the joined utterance.
        await stt.emit(
            TranscriptEvent(text="book an appointment.", is_final=True, speech_final=True)
        )
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)
    finally:
        await runner.close()

    assert llm.user_texts == ["umm book an appointment."]


@pytest.mark.anyio
async def test_media_runner_holds_on_devanagari_trailing_filler() -> None:
    transport = CapturingTransport()
    stt = ScriptedStreamingSpeechToText()
    llm = RecordingLLMClient(response_text="Okay.")
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=RecordingTextToSpeech(),
        transport=transport,
    )

    try:
        await runner.accept_audio(b"\x10\x00" * 200)
        await stt.emit(TranscriptEvent(text="मेरा नंबर मतलब", is_final=True, speech_final=True))
        await asyncio.sleep(0.05)
        assert llm.user_texts == []
        await stt.emit(
            TranscriptEvent(text="मुझे appointment book करनी है।", is_final=True, speech_final=True)
        )
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)
    finally:
        await runner.close()

    assert llm.user_texts == ["मेरा नंबर मतलब मुझे appointment book करनी है।"]


@pytest.mark.anyio
async def test_media_runner_commits_final_segment_on_local_silence_without_speech_final() -> None:
    transport = CapturingTransport()
    stt = ScriptedStreamingSpeechToText()
    llm = RecordingLLMClient(response_text="Okay.")
    snapshots: list[VoiceTurnLatencySnapshot] = []
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=RecordingTextToSpeech(),
        transport=transport,
        local_end_of_turn_silence_seconds=0.0,
        interim_local_silence_commit_enabled=True,
        latency_observer=snapshots.append,
    )

    try:
        await runner.accept_audio(_active_pcm_frame())
        await stt.emit(TranscriptEvent(text="I need service", is_final=True, speech_final=False))
        await asyncio.sleep(0.01)
        await runner.accept_audio(b"\x00\x00" * 320)
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)
    finally:
        await runner.close()

    assert llm.user_texts == ["I need service"]
    assert snapshots[0].turn_commit_source == "local_silence"


@pytest.mark.anyio
async def test_media_runner_commits_when_final_segment_arrives_after_local_silence() -> None:
    transport = CapturingTransport()
    stt = ScriptedStreamingSpeechToText()
    llm = RecordingLLMClient(response_text="Okay.")
    snapshots: list[VoiceTurnLatencySnapshot] = []
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=RecordingTextToSpeech(),
        transport=transport,
        local_end_of_turn_silence_seconds=0.0,
        interim_local_silence_commit_enabled=True,
        latency_observer=snapshots.append,
    )

    try:
        await runner.accept_audio(_active_pcm_frame())
        await runner.accept_audio(b"\x00\x00" * 320)
        await stt.emit(TranscriptEvent(text="I need service", is_final=True, speech_final=False))
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)
    finally:
        await runner.close()

    assert llm.user_texts == ["I need service"]
    assert snapshots[0].turn_commit_source == "local_silence"


@pytest.mark.anyio
async def test_media_runner_fast_commits_short_complete_final_segment() -> None:
    transport = CapturingTransport()
    stt = ScriptedStreamingSpeechToText()
    llm = RecordingLLMClient(response_text="Okay.")
    snapshots: list[VoiceTurnLatencySnapshot] = []
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=RecordingTextToSpeech(),
        transport=transport,
        local_end_of_turn_silence_seconds=0.45,
        latency_observer=snapshots.append,
    )

    try:
        await runner.accept_audio(_active_pcm_frame())
        await stt.emit(TranscriptEvent(text="Monday morning.", is_final=True, speech_final=False))
        await asyncio.sleep(0.25)
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.1)
    finally:
        await runner.close()

    assert llm.user_texts == ["Monday morning."]
    assert snapshots[0].turn_commit_source == "fast_final_local_silence"


@pytest.mark.anyio
async def test_media_runner_commits_interim_transcript_on_local_silence() -> None:
    transport = CapturingTransport()
    stt = ScriptedStreamingSpeechToText()
    llm = RecordingLLMClient(response_text="Okay.")
    snapshots: list[VoiceTurnLatencySnapshot] = []
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=RecordingTextToSpeech(),
        transport=transport,
        local_end_of_turn_silence_seconds=0.0,
        interim_local_silence_commit_enabled=True,
        latency_observer=snapshots.append,
    )

    try:
        await runner.accept_audio(_active_pcm_frame())
        await runner.accept_audio(b"\x00\x00" * 320)
        await stt.emit(TranscriptEvent(text="I need service", is_final=False, speech_final=False))
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)
    finally:
        await runner.close()

    assert llm.user_texts == ["I need service"]
    assert snapshots[0].turn_commit_source == "interim_local_silence"


@pytest.mark.anyio
async def test_media_runner_prefers_speech_final_over_unpunctuated_interim() -> None:
    transport = CapturingTransport()
    stt = ScriptedStreamingSpeechToText()
    llm = RecordingLLMClient(response_text="Okay.")
    snapshots: list[VoiceTurnLatencySnapshot] = []
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=RecordingTextToSpeech(),
        transport=transport,
        local_end_of_turn_silence_seconds=0.05,
        interim_transcript_stability_seconds=0.05,
        latency_observer=snapshots.append,
    )

    try:
        await runner.accept_audio(_active_pcm_frame())
        await stt.emit(
            TranscriptEvent(
                text="I need to book a dental cleaning for Monday",
                is_final=False,
                speech_final=False,
            )
        )
        await asyncio.sleep(0.07)
        assert llm.user_texts == []

        await stt.emit(
            TranscriptEvent(
                text="I need to book a dental cleaning for Monday morning.",
                is_final=True,
                speech_final=True,
            )
        )
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)
    finally:
        await runner.close()

    assert llm.user_texts == ["I need to book a dental cleaning for Monday morning."]
    assert snapshots[0].turn_commit_source == "stt_speech_final"


@pytest.mark.anyio
async def test_media_runner_ignores_duplicate_final_after_interim_local_silence_commit() -> None:
    transport = CapturingTransport()
    stt = ScriptedStreamingSpeechToText()
    llm = RecordingLLMClient(response_text="Okay.")
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=RecordingTextToSpeech(),
        transport=transport,
        local_end_of_turn_silence_seconds=0.0,
        interim_local_silence_commit_enabled=True,
    )

    try:
        await runner.accept_audio(_active_pcm_frame())
        await runner.accept_audio(b"\x00\x00" * 320)
        await stt.emit(TranscriptEvent(text="I need service", is_final=False, speech_final=False))
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)
        await stt.emit(TranscriptEvent(text="I need service", is_final=True, speech_final=True))
        await asyncio.sleep(0.05)
    finally:
        await runner.close()

    assert llm.user_texts == ["I need service"]


@pytest.mark.anyio
async def test_media_runner_prewarms_interim_response_before_local_silence_commit() -> None:
    transport = CapturingTransport()
    stt = ScriptedStreamingSpeechToText()
    llm = RecordingLLMClient(response_text="Prepared response.")
    tts = RecordingTextToSpeech()
    snapshots: list[VoiceTurnLatencySnapshot] = []
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=tts,
        transport=transport,
        local_end_of_turn_silence_seconds=0.08,
        interim_transcript_stability_seconds=0.01,
        interim_local_silence_commit_enabled=True,
        speculative_response_stability_seconds=0.01,
        latency_observer=snapshots.append,
    )

    try:
        await runner.accept_audio(_active_pcm_frame())
        await stt.emit(TranscriptEvent(text="I need service", is_final=False, speech_final=False))
        await asyncio.wait_for(_wait_for_tts_texts(tts, count=1), timeout=0.05)

        assert llm.user_texts == ["I need service"]
        assert tts.texts == ["Prepared response."]
        assert transport.messages == []

        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.2)
    finally:
        await runner.close()

    assert llm.user_texts == ["I need service"]
    assert tts.texts == ["Prepared response."]
    assert _decode_text_audio_payload(transport.messages[0]) == b"Prepared response."
    assert len(snapshots) == 1
    assert snapshots[0].turn_commit_source == "interim_local_silence"
    assert snapshots[0].speculative_response_used
    assert snapshots[0].speculative_response_generation_started_to_commit_ms is not None
    assert snapshots[0].speculative_response_generation_started_to_commit_ms > 0
    assert snapshots[0].speculative_response_tts_first_audio_to_commit_ms is not None
    assert snapshots[0].speculative_response_tts_first_audio_to_commit_ms > 0
    assert snapshots[0].tts_first_audio_to_first_play_audio_ms is not None
    assert snapshots[0].turn_committed_to_first_play_audio_ms is not None
    assert (
        snapshots[0].tts_first_audio_to_first_play_audio_ms
        <= snapshots[0].turn_committed_to_first_play_audio_ms
    )


@pytest.mark.anyio
async def test_media_runner_prewarms_final_segment_before_local_silence_commit() -> None:
    transport = CapturingTransport()
    stt = ScriptedStreamingSpeechToText()
    llm = RecordingLLMClient(response_text="Prepared response.")
    tts = RecordingTextToSpeech()
    snapshots: list[VoiceTurnLatencySnapshot] = []
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=tts,
        transport=transport,
        local_end_of_turn_silence_seconds=0.08,
        interim_transcript_stability_seconds=0.01,
        speculative_response_stability_seconds=0.0,
        latency_observer=snapshots.append,
    )

    try:
        await runner.accept_audio(_active_pcm_frame())
        await stt.emit(TranscriptEvent(text="I need service", is_final=True, speech_final=False))
        await asyncio.wait_for(_wait_for_tts_texts(tts, count=1), timeout=0.05)

        assert llm.user_texts == ["I need service"]
        assert tts.texts == ["Prepared response."]
        assert transport.messages == []

        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.2)
    finally:
        await runner.close()

    assert llm.user_texts == ["I need service"]
    assert tts.texts == ["Prepared response."]
    assert _decode_text_audio_payload(transport.messages[0]) == b"Prepared response."
    assert len(snapshots) == 1
    assert snapshots[0].turn_commit_source == "local_silence"
    assert snapshots[0].speculative_response_used
    assert snapshots[0].speculative_response_started_count == 1
    assert snapshots[0].speculative_response_discarded_count == 0
    assert snapshots[0].speculative_response_tts_first_audio_to_commit_ms is not None
    assert snapshots[0].speculative_response_tts_first_audio_to_commit_ms > 0


@pytest.mark.anyio
async def test_media_runner_skips_speculative_prewarm_for_tiny_interim_text() -> None:
    transport = CapturingTransport()
    stt = ScriptedStreamingSpeechToText()
    llm = RecordingLLMClient(response_text="Prepared response.")
    tts = RecordingTextToSpeech()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=tts,
        transport=transport,
        interim_transcript_stability_seconds=0.0,
        speculative_response_stability_seconds=0.0,
    )

    try:
        await runner.accept_audio(_active_pcm_frame())
        await stt.emit(TranscriptEvent(text="Mumbai", is_final=False, speech_final=False))
        await asyncio.sleep(0.03)
    finally:
        await runner.close()

    assert llm.user_texts == []
    assert tts.texts == []
    assert transport.messages == []


@pytest.mark.anyio
async def test_media_runner_defers_speculative_history_until_answer_is_used() -> None:
    transport = CapturingTransport()
    stt = ScriptedStreamingSpeechToText()
    llm = HistoryAwareRecordingLLMClient(
        responses={
            "I need service": "Service response.",
            "I need billing help": "Billing response.",
        }
    )
    tts = RecordingTextToSpeech()
    snapshots: list[VoiceTurnLatencySnapshot] = []
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=tts,
        transport=transport,
        local_end_of_turn_silence_seconds=0.2,
        interim_transcript_stability_seconds=0.0,
        interim_local_silence_commit_enabled=True,
        speculative_response_stability_seconds=0.0,
        latency_observer=snapshots.append,
    )

    try:
        await runner.accept_audio(_active_pcm_frame())
        await stt.emit(TranscriptEvent(text="I need service", is_final=False, speech_final=False))
        await asyncio.wait_for(_wait_for_tts_texts(tts, count=1), timeout=0.05)

        assert llm.calls == [("I need service", False)]
        assert llm.remembered_turns == []

        await stt.emit(
            TranscriptEvent(text="I need billing help", is_final=False, speech_final=False)
        )
        await asyncio.wait_for(_wait_for_tts_texts(tts, count=2), timeout=0.05)
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)
    finally:
        await runner.close()

    assert llm.calls == [("I need service", False), ("I need billing help", False)]
    assert llm.remembered_turns == [("I need billing help", "Billing response.")]
    assert _decode_text_audio_payload(transport.messages[0]) == b"Billing response."
    assert len(snapshots) == 1
    assert snapshots[0].speculative_response_started_count == 2
    assert snapshots[0].speculative_response_discarded_count == 1


@pytest.mark.anyio
async def test_media_runner_reuses_speculative_response_when_final_adds_punctuation() -> None:
    transport = CapturingTransport()
    stt = ScriptedStreamingSpeechToText()
    llm = HistoryAwareRecordingLLMClient(responses={"I need service": "Service response."})
    tts = RecordingTextToSpeech()
    snapshots: list[VoiceTurnLatencySnapshot] = []
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=tts,
        transport=transport,
        interim_transcript_stability_seconds=0.0,
        speculative_response_stability_seconds=0.0,
        latency_observer=snapshots.append,
    )

    try:
        await runner.accept_audio(_active_pcm_frame())
        await stt.emit(TranscriptEvent(text="I need service", is_final=False, speech_final=False))
        await asyncio.wait_for(_wait_for_tts_texts(tts, count=1), timeout=0.05)

        await stt.emit(TranscriptEvent(text="I need service.", is_final=True, speech_final=True))
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)
    finally:
        await runner.close()

    assert llm.calls == [("I need service", False)]
    assert llm.remembered_turns == [("I need service.", "Service response.")]
    assert _decode_text_audio_payload(transport.messages[0]) == b"Service response."
    assert len(snapshots) == 1
    assert snapshots[0].speculative_response_used
    assert snapshots[0].speculative_response_started_count == 1
    assert snapshots[0].speculative_response_discarded_count == 0


@pytest.mark.anyio
async def test_media_runner_records_spoken_speculative_transcript_when_interrupted() -> None:
    updates: list[str] = []
    transport = CapturingTransport()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=ScriptedStreamingSpeechToText(),
        llm=RecordingLLMClient(response_text="unused"),
        tts=RecordingTextToSpeech(),
        transport=transport,
        transcript_observer=updates.append,
    )

    async def wait_forever() -> None:
        await asyncio.Event().wait()

    speculative_task = asyncio.create_task(wait_forever())
    runner._generation_epoch = 1
    runner._transcript_turn_index = 1
    runner._transcript_turn_by_epoch[1] = 1
    runner._record_transcript_turn("user", "I need service", turn=1)
    runner._speculative_response_task = speculative_task
    runner._speculative_response_text = "I need service"
    runner._speculative_response_audio_chunks = [_text_audio("Spoken partial response.")]
    runner._speculative_response_spoken_text_parts = ["Spoken partial response."]
    runner._speculative_response_generation_started = True
    runner._speculative_response_generation_started_at = time.perf_counter()

    try:
        play_task = asyncio.create_task(runner._try_play_speculative_response("I need service", 1))
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.2)

        play_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await play_task
    finally:
        if not speculative_task.done():
            speculative_task.cancel()
        with suppress(asyncio.CancelledError):
            await speculative_task
        await runner.close()

    assert _decode_text_audio_payload(transport.messages[0]) == b"Spoken partial response."
    assert json.loads(updates[-1]) == [
        {"role": "user", "text": "I need service", "turn": 1},
        {"role": "assistant", "text": "Spoken partial response.", "turn": 1},
    ]


@pytest.mark.anyio
async def test_media_runner_keeps_speculative_response_for_punctuation_only_revision() -> None:
    transport = CapturingTransport()
    stt = ScriptedStreamingSpeechToText()
    llm = HistoryAwareRecordingLLMClient(responses={"I need service": "Service response."})
    tts = RecordingTextToSpeech()
    snapshots: list[VoiceTurnLatencySnapshot] = []
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=tts,
        transport=transport,
        interim_transcript_stability_seconds=0.0,
        speculative_response_stability_seconds=0.0,
        latency_observer=snapshots.append,
    )

    try:
        await runner.accept_audio(_active_pcm_frame())
        await stt.emit(TranscriptEvent(text="I need service", is_final=False, speech_final=False))
        await asyncio.wait_for(_wait_for_tts_texts(tts, count=1), timeout=0.05)

        await stt.emit(TranscriptEvent(text="I need service.", is_final=False, speech_final=False))
        await stt.emit(TranscriptEvent(text="I need service.", is_final=True, speech_final=True))
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)
    finally:
        await runner.close()

    assert llm.calls == [("I need service", False)]
    assert llm.remembered_turns == [("I need service.", "Service response.")]
    assert _decode_text_audio_payload(transport.messages[0]) == b"Service response."
    assert len(snapshots) == 1
    assert snapshots[0].speculative_response_used
    assert snapshots[0].speculative_response_started_count == 1
    assert snapshots[0].speculative_response_discarded_count == 0


@pytest.mark.anyio
async def test_media_runner_counts_revised_speculative_interim_as_discarded() -> None:
    transport = CapturingTransport()
    stt = ScriptedStreamingSpeechToText()
    llm = HistoryAwareRecordingLLMClient(
        responses={
            "I need service": "Service response.",
            "I need billing help": "Billing response.",
        }
    )
    tts = RecordingTextToSpeech()
    snapshots: list[VoiceTurnLatencySnapshot] = []
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=tts,
        transport=transport,
        interim_transcript_stability_seconds=0.0,
        speculative_response_stability_seconds=0.0,
        latency_observer=snapshots.append,
    )

    try:
        await runner.accept_audio(_active_pcm_frame())
        await stt.emit(TranscriptEvent(text="I need service", is_final=False, speech_final=False))
        await asyncio.wait_for(_wait_for_tts_texts(tts, count=1), timeout=0.05)

        await stt.emit(
            TranscriptEvent(text="I need billing help", is_final=True, speech_final=True)
        )
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)
    finally:
        await runner.close()

    assert llm.calls == [("I need service", False), ("I need billing help", True)]
    assert llm.remembered_turns == [("I need billing help", "Billing response.")]
    assert _decode_text_audio_payload(transport.messages[0]) == b"Billing response."
    assert len(snapshots) == 1
    assert not snapshots[0].speculative_response_used
    assert snapshots[0].speculative_response_started_count == 1
    assert snapshots[0].speculative_response_discarded_count == 1


@pytest.mark.anyio
async def test_media_runner_keeps_unchanged_speculative_response_across_voice_frames() -> None:
    transport = CapturingTransport()
    stt = ScriptedStreamingSpeechToText()
    llm = RecordingLLMClient(response_text="Prepared response.")
    tts = RecordingTextToSpeech()
    snapshots: list[VoiceTurnLatencySnapshot] = []
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=tts,
        transport=transport,
        local_end_of_turn_silence_seconds=0.06,
        interim_transcript_stability_seconds=0.0,
        interim_local_silence_commit_enabled=True,
        speculative_response_stability_seconds=0.0,
        latency_observer=snapshots.append,
    )

    try:
        await runner.accept_audio(_active_pcm_frame())
        await stt.emit(TranscriptEvent(text="I need service", is_final=False, speech_final=False))
        await asyncio.wait_for(_wait_for_tts_texts(tts, count=1), timeout=0.05)

        await runner.accept_audio(_active_pcm_frame())
        assert transport.messages == []

        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.2)
    finally:
        await runner.close()

    assert llm.user_texts == ["I need service"]
    assert tts.texts == ["Prepared response."]
    assert len(snapshots) == 1
    assert snapshots[0].speculative_response_used
    assert snapshots[0].speculative_response_tts_first_audio_to_commit_ms is not None
    assert snapshots[0].speculative_response_tts_first_audio_to_commit_ms > 0


@pytest.mark.anyio
async def test_media_runner_uses_independent_speculative_prewarm_stability() -> None:
    transport = CapturingTransport()
    stt = ScriptedStreamingSpeechToText()
    llm = RecordingLLMClient(response_text="Prepared response.")
    tts = RecordingTextToSpeech()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=tts,
        transport=transport,
        local_end_of_turn_silence_seconds=0.08,
        interim_transcript_stability_seconds=0.05,
        speculative_response_stability_seconds=0.0,
    )

    try:
        await runner.accept_audio(_active_pcm_frame())
        await stt.emit(TranscriptEvent(text="I need service", is_final=False, speech_final=False))
        await asyncio.wait_for(_wait_for_tts_texts(tts, count=1), timeout=0.03)
    finally:
        await runner.close()

    assert llm.user_texts == ["I need service"]
    assert tts.texts == ["Prepared response."]
    assert transport.messages == []


@pytest.mark.anyio
async def test_media_runner_promotes_inflight_speculative_response_without_duplicate_llm() -> None:
    transport = CapturingTransport()
    stt = ScriptedStreamingSpeechToText()
    llm = FirstCallOnlyReleasableLLMClient(response_text="Promoted response.")
    tts = DelayedBlockingFirstChunkTextToSpeech(delay_seconds=0.03)
    snapshots: list[VoiceTurnLatencySnapshot] = []
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=tts,
        transport=transport,
        local_end_of_turn_silence_seconds=0.03,
        interim_transcript_stability_seconds=0.0,
        interim_local_silence_commit_enabled=True,
        speculative_response_stability_seconds=0.0,
        turn_timeout_seconds=1.0,
        latency_observer=snapshots.append,
    )

    try:
        await runner.accept_audio(_active_pcm_frame())
        await stt.emit(TranscriptEvent(text="I need service", is_final=False, speech_final=False))
        await asyncio.wait_for(llm.started.wait(), timeout=0.2)
        await asyncio.sleep(0.05)
        assert transport.messages == []

        llm.release.set()
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.4)
    finally:
        tts.release.set()
        await runner.close()

    assert llm.user_texts == ["I need service"]
    assert tts.texts == ["Promoted response."]
    assert _decode_text_audio_payload(transport.messages[0]) == b"Promoted response."
    assert len(snapshots) == 1
    assert snapshots[0].speculative_response_used
    assert snapshots[0].tts_first_audio_to_first_play_audio_ms is not None
    assert snapshots[0].turn_committed_to_first_play_audio_ms is not None
    assert snapshots[0].llm_first_chunk_to_tts_first_audio_ms is not None
    assert snapshots[0].llm_first_chunk_to_tts_first_audio_ms >= 20.0
    assert (
        snapshots[0].tts_first_audio_to_first_play_audio_ms
        <= snapshots[0].turn_committed_to_first_play_audio_ms
    )


@pytest.mark.anyio
async def test_media_runner_promotes_scheduled_speculative_response_at_commit() -> None:
    transport = CapturingTransport()
    stt = ScriptedStreamingSpeechToText()
    llm = RecordingLLMClient(response_text="Promoted response.")
    tts = RecordingTextToSpeech()
    snapshots: list[VoiceTurnLatencySnapshot] = []
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=tts,
        transport=transport,
        local_end_of_turn_silence_seconds=0.03,
        interim_transcript_stability_seconds=0.0,
        interim_local_silence_commit_enabled=True,
        speculative_response_stability_seconds=10.0,
        turn_timeout_seconds=1.0,
        latency_observer=snapshots.append,
    )

    try:
        await runner.accept_audio(_active_pcm_frame())
        await stt.emit(TranscriptEvent(text="I need service", is_final=False, speech_final=False))
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.2)
    finally:
        await runner.close()

    assert llm.user_texts == ["I need service"]
    assert tts.texts == ["Promoted response."]
    assert _decode_text_audio_payload(transport.messages[0]) == b"Promoted response."
    assert len(snapshots) == 1
    assert snapshots[0].speculative_response_used


@pytest.mark.anyio
async def test_media_runner_plays_ack_while_promoted_speculative_response_waits() -> None:
    transport = CapturingTransport()
    stt = ScriptedStreamingSpeechToText()
    llm = FirstCallOnlyReleasableLLMClient(response_text="Promoted response.")
    tts = RecordingTextToSpeech()
    snapshots: list[VoiceTurnLatencySnapshot] = []
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=tts,
        transport=transport,
        local_end_of_turn_silence_seconds=0.05,
        interim_transcript_stability_seconds=0.0,
        interim_local_silence_commit_enabled=True,
        speculative_response_stability_seconds=0.0,
        pre_response_ack_text="One moment.",
        pre_response_ack_delay_seconds=0.01,
        turn_timeout_seconds=1.0,
        latency_observer=snapshots.append,
    )

    try:
        await runner.accept_audio(_active_pcm_frame())
        await stt.emit(TranscriptEvent(text="I need service", is_final=False, speech_final=False))
        await asyncio.wait_for(llm.started.wait(), timeout=0.2)
        assert runner._speculative_response_text == "I need service"
        assert runner._speculative_response_task is not None

        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.2)
        assert _decode_text_audio_payload(transport.messages[0]) == b"One moment."
        assert tts.texts == ["One moment."]
        assert snapshots == []

        llm.release.set()
        await asyncio.wait_for(_wait_for_messages(transport, count=2), timeout=0.2)
    finally:
        await runner.close()

    assert llm.user_texts == ["I need service"]
    assert tts.texts == ["One moment.", "Promoted response."]
    assert _decode_text_audio_payload(transport.messages[1]) == b"Promoted response."
    assert len(snapshots) == 1
    assert snapshots[0].pre_response_ack_used
    assert snapshots[0].speculative_response_used
    assert snapshots[0].turn_committed_to_pre_response_ack_ms is not None
    assert snapshots[0].turn_committed_to_answer_first_play_audio_ms is not None


@pytest.mark.anyio
async def test_media_runner_credits_speculative_wait_toward_ack_delay() -> None:
    transport = CapturingTransport()
    stt = ScriptedStreamingSpeechToText()
    llm = FirstCallOnlyReleasableLLMClient(response_text="Promoted response.")
    tts = RecordingTextToSpeech()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=tts,
        transport=transport,
        local_end_of_turn_silence_seconds=0.2,
        interim_transcript_stability_seconds=0.0,
        interim_local_silence_commit_enabled=True,
        speculative_response_stability_seconds=0.0,
        pre_response_ack_text="One moment.",
        pre_response_ack_delay_seconds=10.0,
        turn_timeout_seconds=1.0,
    )

    try:
        await runner.accept_audio(_active_pcm_frame())
        await stt.emit(TranscriptEvent(text="I need service", is_final=False, speech_final=False))
        await asyncio.wait_for(llm.started.wait(), timeout=0.2)
        runner._speculative_response_generation_started_at = time.perf_counter() - 10.0

        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.4)
        assert _decode_text_audio_payload(transport.messages[0]) == b"One moment."

        llm.release.set()
        await asyncio.wait_for(_wait_for_messages(transport, count=2), timeout=0.2)
    finally:
        await runner.close()

    assert llm.user_texts == ["I need service"]
    assert tts.texts == ["One moment.", "Promoted response."]


@pytest.mark.anyio
async def test_media_runner_local_silence_does_not_commit_incomplete_segment() -> None:
    transport = CapturingTransport()
    stt = ScriptedStreamingSpeechToText()
    llm = RecordingLLMClient(response_text="Okay.")
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=RecordingTextToSpeech(),
        transport=transport,
        local_end_of_turn_silence_seconds=0.0,
    )

    try:
        await runner.accept_audio(_active_pcm_frame())
        await stt.emit(TranscriptEvent(text="my number is 98", is_final=True, speech_final=False))
        await asyncio.sleep(0.01)
        await runner.accept_audio(b"\x00\x00" * 320)
        await asyncio.sleep(0.05)
        assert llm.user_texts == []

        await stt.emit(TranscriptEvent(text="76.", is_final=True, speech_final=False))
        await asyncio.sleep(0.01)
        await runner.accept_audio(b"\x00\x00" * 320)
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)
    finally:
        await runner.close()

    assert llm.user_texts == ["my number is 98 76."]


@pytest.mark.anyio
async def test_media_runner_streams_first_sentence_to_tts_before_full_reply() -> None:
    transport = CapturingTransport()
    tts = RecordingTextToSpeech()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="hello"),
        llm=ChunkedRecordingLLMClient(chunks=["I can help. ", "What day works?"]),
        tts=tts,
        transport=transport,
    )

    try:
        await runner.accept_audio(b"caller audio")
    finally:
        await runner.close()

    # Each sentence is synthesized separately, in order (first flushed mid-stream).
    assert tts.texts == ["I can help.", "What day works?"]


@pytest.mark.anyio
async def test_media_runner_closes_call_after_end_marker_and_closing_script() -> None:
    tts = RecordingTextToSpeech()
    hangups: list[str] = []
    transcripts: list[str] = []
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="book it"),
        llm=RecordingLLMClient(response_text="Your appointment is booked. [[END_CALL]]"),
        tts=tts,
        transport=CapturingTransport(),
        closing_message="Thanks for calling Acme Dental. Goodbye.",
        hangup_observer=hangups.append,
        transcript_observer=transcripts.append,
    )

    await runner.accept_audio(b"caller audio")
    await runner.close()

    assert tts.texts == [
        "Your appointment is booked.",
        "Thanks for calling Acme Dental. Goodbye.",
    ]
    assert hangups == ["call-1"]
    assert "[[END_CALL]]" not in transcripts[-1]


@pytest.mark.anyio
async def test_media_runner_strips_action_directive_and_emits_request() -> None:
    tts = RecordingTextToSpeech()
    actions: list[VoiceActionRequest] = []
    transcripts: list[str] = []
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="book it"),
        llm=RecordingLLMClient(
            response_text=(
                "Your appointment is booked. "
                '[[ACTION:{"type":"appointment.confirm",'
                '"payload":{"appointment_id":"a-1"}}]]'
            )
        ),
        tts=tts,
        transport=CapturingTransport(),
        transcript_observer=transcripts.append,
        voice_action_observer=actions.append,
    )

    await runner.accept_audio(b"caller audio")
    await runner.close()

    assert tts.texts == ["Your appointment is booked."]
    assert len(actions) == 1
    assert actions[0].call_id == "call-1"
    assert actions[0].stream_id == "stream-1"
    assert isinstance(actions[0].turn_epoch, int)
    assert actions[0].action_type == "appointment.confirm"
    assert actions[0].payload == {"appointment_id": "a-1"}
    assert "[[ACTION:" not in transcripts[-1]


@pytest.mark.anyio
async def test_media_runner_buffers_split_action_directive_before_tts() -> None:
    tts = RecordingTextToSpeech()
    actions: list[VoiceActionRequest] = []
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="hold the slot"),
        llm=ChunkedRecordingLLMClient(
            chunks=[
                "I found the slot. [[ACT",
                'ION:{"type":"appointment.hold","payload":{"slot":"9 AM"}}]]',
            ]
        ),
        tts=tts,
        transport=CapturingTransport(),
        voice_action_observer=actions.append,
    )

    await runner.accept_audio(b"caller audio")
    await runner.close()

    assert tts.texts == ["I found the slot."]
    assert [action.action_type for action in actions] == ["appointment.hold"]
    assert actions[0].payload == {"slot": "9 AM"}


@pytest.mark.anyio
async def test_media_runner_drops_incomplete_action_directive_without_speaking_it() -> None:
    tts = RecordingTextToSpeech()
    actions: list[VoiceActionRequest] = []
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="start workflow"),
        llm=RecordingLLMClient(response_text='I started it. [[ACTION:{"type":"workflow.start"'),
        tts=tts,
        transport=CapturingTransport(),
        voice_action_observer=actions.append,
    )

    await runner.accept_audio(b"caller audio")
    await runner.close()

    assert tts.texts == ["I started it."]
    assert actions == []


@pytest.mark.anyio
async def test_media_runner_flushes_first_safe_phrase_before_full_sentence() -> None:
    transport = CapturingTransport()
    llm = GatedSecondChunkLLMClient(
        first_chunk="I can help with that, ",
        second_chunk="let me check.",
    )
    tts = RecordingTextToSpeech()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="hello"),
        llm=llm,
        tts=tts,
        transport=transport,
    )

    try:
        turn_task = asyncio.create_task(runner.accept_audio(b"caller audio"))
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)
        assert tts.texts == ["I can help with that,"]
        assert not turn_task.done()

        llm.release_second_chunk.set()
        await asyncio.wait_for(turn_task, timeout=0.5)
    finally:
        await runner.close()

    assert tts.texts == ["I can help with that,", "let me check."]


@pytest.mark.anyio
async def test_media_runner_flushes_safe_terminal_first_phrase_before_next_chunk() -> None:
    transport = CapturingTransport()
    llm = GatedSecondChunkLLMClient(
        first_chunk="Your appointment is ready. ",
        second_chunk=" What day works?",
    )
    tts = RecordingTextToSpeech()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="hello"),
        llm=llm,
        tts=tts,
        transport=transport,
    )

    try:
        turn_task = asyncio.create_task(runner.accept_audio(b"caller audio"))
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)
        assert tts.texts == ["Your appointment is ready."]
        assert not turn_task.done()

        llm.release_second_chunk.set()
        await asyncio.wait_for(turn_task, timeout=0.5)
    finally:
        await runner.close()

    assert tts.texts == ["Your appointment is ready.", "What day works?"]


@pytest.mark.anyio
async def test_media_runner_reads_ahead_while_first_reply_chunk_is_in_tts() -> None:
    transport = CapturingTransport()
    llm = GatedSecondChunkLLMClient(
        first_chunk="I can help with that, ",
        second_chunk="let me check.",
    )
    tts = BlockingFirstSynthesisTextToSpeech()
    snapshots: list[VoiceTurnLatencySnapshot] = []
    snapshot_added = asyncio.Event()

    def observe_latency(snapshot: VoiceTurnLatencySnapshot) -> None:
        snapshots.append(snapshot)
        snapshot_added.set()

    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="hello"),
        llm=llm,
        tts=tts,
        transport=transport,
        latency_observer=observe_latency,
    )

    try:
        turn_task = asyncio.create_task(runner.accept_audio(b"caller audio"))
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)

        assert tts.texts == ["I can help with that,"]
        assert not turn_task.done()

        llm.release_second_chunk.set()
        await asyncio.wait_for(snapshot_added.wait(), timeout=0.5)

        tts.release_first_synthesis.set()
        await asyncio.wait_for(turn_task, timeout=0.5)
    finally:
        tts.release_first_synthesis.set()
        await runner.close()

    assert tts.texts == ["I can help with that,", "let me check."]
    assert len(snapshots) == 1
    assert snapshots[0].answer_first_play_audio_to_llm_stream_completed_ms is not None
    assert snapshots[0].answer_first_play_audio_to_llm_stream_completed_ms > 0


@pytest.mark.anyio
async def test_media_runner_soft_flushes_first_safe_phrase_without_punctuation() -> None:
    transport = CapturingTransport()
    llm = GatedSecondChunkLLMClient(
        first_chunk="I can help with your appointment today ",
        second_chunk="let me check the calendar.",
    )
    tts = RecordingTextToSpeech()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="hello"),
        llm=llm,
        tts=tts,
        transport=transport,
    )

    try:
        turn_task = asyncio.create_task(runner.accept_audio(b"caller audio"))
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)
        assert tts.texts == ["I can help with your appointment today"]
        assert not turn_task.done()

        llm.release_second_chunk.set()
        await asyncio.wait_for(turn_task, timeout=0.5)
    finally:
        await runner.close()

    assert tts.texts == [
        "I can help with your appointment today",
        "let me check the calendar.",
    ]


@pytest.mark.anyio
async def test_media_runner_prefetches_next_answer_tts_while_playing_current_chunk() -> None:
    transport = BlockingFirstPlayAudioTransport()
    llm = GatedSecondChunkLLMClient(
        first_chunk="I can help with your appointment today ",
        second_chunk="let me check the calendar.",
    )
    tts = RecordingTextToSpeech()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="hello"),
        llm=llm,
        tts=tts,
        transport=transport,
    )

    try:
        turn_task = asyncio.create_task(runner.accept_audio(b"caller audio"))
        await asyncio.wait_for(transport.first_play_audio_started.wait(), timeout=0.5)
        assert tts.texts == ["I can help with your appointment today"]
        assert not turn_task.done()

        llm.release_second_chunk.set()
        await asyncio.wait_for(_wait_for_tts_texts(tts, count=2), timeout=0.5)
        assert tts.texts == [
            "I can help with your appointment today",
            "let me check the calendar.",
        ]
        assert not turn_task.done()

        transport.release_first_play_audio.set()
        await asyncio.wait_for(turn_task, timeout=0.5)
    finally:
        transport.release_first_play_audio.set()
        await runner.close()


@pytest.mark.anyio
async def test_media_runner_flushes_first_answer_phrase_after_pre_response_ack() -> None:
    transport = CapturingTransport()
    llm = DelayedGatedSecondChunkLLMClient(
        first_chunk="We are closed on Sundays, ",
        second_chunk="but we are open Monday morning.",
    )
    tts = RecordingTextToSpeech()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="hello"),
        llm=llm,
        tts=tts,
        transport=transport,
        pre_response_ack_text="One moment.",
        pre_response_ack_delay_seconds=0.01,
    )

    try:
        turn_task = asyncio.create_task(runner.accept_audio(b"caller audio"))
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)
        assert tts.texts == ["One moment."]

        llm.release_first_chunk.set()
        await asyncio.wait_for(_wait_for_messages(transport, count=2), timeout=0.5)
        assert tts.texts == ["One moment.", "We are closed on Sundays,"]
        assert not turn_task.done()

        llm.release_second_chunk.set()
        await asyncio.wait_for(turn_task, timeout=0.5)
    finally:
        llm.release_first_chunk.set()
        llm.release_second_chunk.set()
        await runner.close()

    assert tts.texts == [
        "One moment.",
        "We are closed on Sundays,",
        "but we are open Monday morning.",
    ]


@pytest.mark.anyio
async def test_media_runner_skips_duplicate_ack_phrase_after_pre_response_ack() -> None:
    transport = CapturingTransport()
    llm = DelayedGatedSecondChunkLLMClient(
        first_chunk="Sure, we are closed on Sundays, ",
        second_chunk="but we are open Monday morning.",
    )
    tts = RecordingTextToSpeech()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="hello"),
        llm=llm,
        tts=tts,
        transport=transport,
        pre_response_ack_text="Sure.",
        pre_response_ack_delay_seconds=0.01,
    )

    try:
        turn_task = asyncio.create_task(runner.accept_audio(b"caller audio"))
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)
        assert tts.texts == ["Sure."]

        llm.release_first_chunk.set()
        await asyncio.wait_for(_wait_for_messages(transport, count=2), timeout=0.5)
        assert tts.texts == ["Sure.", "we are closed on Sundays,"]
        assert not turn_task.done()

        llm.release_second_chunk.set()
        await asyncio.wait_for(turn_task, timeout=0.5)
    finally:
        llm.release_first_chunk.set()
        llm.release_second_chunk.set()
        await runner.close()

    assert tts.texts == [
        "Sure.",
        "we are closed on Sundays,",
        "but we are open Monday morning.",
    ]


@pytest.mark.anyio
async def test_media_runner_speaks_pre_response_ack_when_llm_first_chunk_is_slow() -> None:
    transport = CapturingTransport()
    llm = DelayedFirstChunkLLMClient(response_text="The answer is ready.")
    tts = RecordingTextToSpeech()
    snapshots: list[VoiceTurnLatencySnapshot] = []
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="hello"),
        llm=llm,
        tts=tts,
        transport=transport,
        pre_response_ack_text="One moment.",
        pre_response_ack_delay_seconds=0.01,
        latency_observer=snapshots.append,
    )

    try:
        turn_task = asyncio.create_task(runner.accept_audio(b"caller audio"))
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)
        assert tts.texts == ["One moment."]
        assert not llm.first_chunk_yielded.is_set()
        assert snapshots == []

        llm.release_first_chunk.set()
        await asyncio.wait_for(turn_task, timeout=0.5)
    finally:
        await runner.close()

    assert tts.texts == ["One moment.", "The answer is ready."]
    assert len(snapshots) == 1
    assert snapshots[0].pre_response_ack_used
    assert snapshots[0].turn_committed_to_pre_response_ack_ms is not None
    assert snapshots[0].turn_committed_to_answer_first_play_audio_ms is not None


@pytest.mark.anyio
async def test_media_runner_reuses_cached_pre_response_ack_audio_after_first_synthesis() -> None:
    transport = CapturingTransport()
    llm = ReleasableDelayedLLMClient(response_text="The answer is ready.")
    tts = RecordingTextToSpeech()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="hello"),
        llm=llm,
        tts=tts,
        transport=transport,
        pre_response_ack_text="One moment.",
        pre_response_ack_delay_seconds=0.01,
    )

    try:
        first_turn = asyncio.create_task(runner.accept_audio(b"caller audio"))
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)
        assert _decode_text_audio_payload(transport.messages[0]) == b"One moment."

        llm.release_turn(0)
        await asyncio.wait_for(_wait_for_messages(transport, count=2), timeout=0.5)
        await asyncio.wait_for(first_turn, timeout=0.5)

        second_turn = asyncio.create_task(runner.accept_audio(b"caller audio"))
        await asyncio.wait_for(_wait_for_messages(transport, count=4), timeout=0.5)
        assert transport.messages[2]["event"] == "clearAudio"
        assert _decode_text_audio_payload(transport.messages[3]) == b"One moment."

        llm.release_turn(1)
        await asyncio.wait_for(_wait_for_messages(transport, count=5), timeout=0.5)
        await asyncio.wait_for(second_turn, timeout=0.5)
    finally:
        await runner.close()

    assert tts.texts == [
        "One moment.",
        "The answer is ready.",
        "The answer is ready.",
    ]
    assert _decode_text_audio_payload(transport.messages[1]) == b"The answer is ready."
    assert _decode_text_audio_payload(transport.messages[4]) == b"The answer is ready."


@pytest.mark.anyio
async def test_media_runner_prewarms_pre_response_ack_audio_on_start() -> None:
    transport = CapturingTransport()
    llm = DelayedFirstChunkLLMClient(response_text="The answer is ready.")
    tts = RecordingTextToSpeech()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="hello"),
        llm=llm,
        tts=tts,
        transport=transport,
        pre_response_ack_text="One moment.",
        pre_response_ack_delay_seconds=0.01,
    )

    try:
        await runner.start()
        for _ in range(50):
            if tts.texts:
                break
            await asyncio.sleep(0.01)
        assert tts.texts == ["One moment."]
        assert runner._pre_response_ack_audio is not None

        turn_task = asyncio.create_task(runner.accept_audio(b"caller audio"))
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)
        assert _decode_text_audio_payload(transport.messages[0]) == b"One moment."
        assert tts.texts == ["One moment."]

        llm.release_first_chunk.set()
        await asyncio.wait_for(turn_task, timeout=0.5)
    finally:
        await runner.close()

    assert tts.texts == ["One moment.", "The answer is ready."]


@pytest.mark.anyio
async def test_media_runner_uses_shared_audio_cache_for_pre_response_ack() -> None:
    from voice_agent.workflow.audio_cache import AudioCacheContext, InMemoryAudioCache

    cache = InMemoryAudioCache()
    context = AudioCacheContext(tts_provider="local")

    first_runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="hello"),
        llm=DelayedFirstChunkLLMClient(response_text="The answer is ready."),
        tts=RecordingTextToSpeech(),
        transport=CapturingTransport(),
        pre_response_ack_text="One moment.",
        pre_response_ack_delay_seconds=0.01,
        audio_cache=cache,
        audio_cache_context=context,
    )

    try:
        await first_runner.start()
        for _ in range(50):
            if cache.set_count:
                break
            await asyncio.sleep(0.01)
        assert cache.set_count == 1
    finally:
        await first_runner.close()

    transport = CapturingTransport()
    llm = DelayedFirstChunkLLMClient(response_text="The answer is ready.")
    tts = RecordingTextToSpeech()
    snapshots: list[VoiceTurnLatencySnapshot] = []
    second_runner = MediaSessionRunner(
        call_id="call-2",
        stream_id="stream-2",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="hello"),
        llm=llm,
        tts=tts,
        transport=transport,
        pre_response_ack_text="One moment.",
        pre_response_ack_delay_seconds=0.01,
        audio_cache=cache,
        audio_cache_context=context,
        latency_observer=snapshots.append,
    )

    try:
        await second_runner.start()
        assert tts.texts == []

        turn_task = asyncio.create_task(second_runner.accept_audio(b"caller audio"))
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)
        assert _decode_text_audio_payload(transport.messages[0]) == b"One moment."
        assert tts.texts == []

        llm.release_first_chunk.set()
        await asyncio.wait_for(turn_task, timeout=0.5)
    finally:
        await second_runner.close()

    assert tts.texts == ["The answer is ready."]
    assert len(snapshots) == 1
    assert snapshots[0].pre_response_ack_used
    assert snapshots[0].pre_response_ack_cache_hit is True


@pytest.mark.anyio
async def test_media_runner_does_not_wait_for_unfinished_pre_response_ack_prewarm() -> None:
    transport = CapturingTransport()
    llm = DelayedFirstChunkLLMClient(response_text="The answer is ready.")
    tts = HangingPrewarmThenFreshAckTextToSpeech(
        ack_text="One moment.",
        answer_text="The answer is ready.",
    )
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="hello"),
        llm=llm,
        tts=tts,
        transport=transport,
        turn_timeout_seconds=1.0,
        pre_response_ack_text="One moment.",
        pre_response_ack_delay_seconds=0.01,
    )

    try:
        await runner.start()
        await asyncio.wait_for(tts.prewarm_started.wait(), timeout=0.5)

        turn_task = asyncio.create_task(runner.accept_audio(b"caller audio"))
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)
        assert _decode_text_audio_payload(transport.messages[0]) == b"One moment."
        assert tts.prewarm_cancelled.is_set()

        llm.release_first_chunk.set()
        await asyncio.wait_for(turn_task, timeout=0.5)
    finally:
        llm.release_first_chunk.set()
        await runner.close()

    assert tts.texts == ["One moment.", "One moment.", "The answer is ready."]


@pytest.mark.anyio
async def test_media_runner_attributes_tts_latency_to_answer_not_pre_response_ack() -> None:
    transport = CapturingTransport()
    llm = DelayedFirstChunkLLMClient(response_text="The answer is ready.")
    tts = DelayedAnswerTextToSpeech(answer_text="The answer is ready.", delay_seconds=0.02)
    snapshots: list[VoiceTurnLatencySnapshot] = []
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="hello"),
        llm=llm,
        tts=tts,
        transport=transport,
        pre_response_ack_text="One moment.",
        pre_response_ack_delay_seconds=0.01,
        latency_observer=snapshots.append,
    )

    try:
        turn_task = asyncio.create_task(runner.accept_audio(b"caller audio"))
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)
        assert tts.texts == ["One moment."]
        assert snapshots == []

        llm.release_first_chunk.set()
        await asyncio.wait_for(turn_task, timeout=0.5)
    finally:
        await runner.close()

    assert tts.texts == ["One moment.", "The answer is ready."]
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.pre_response_ack_used
    assert snapshot.turn_committed_to_pre_response_ack_ms is not None
    assert snapshot.llm_first_chunk_to_tts_first_audio_ms is not None
    assert snapshot.llm_first_chunk_to_tts_first_audio_ms >= 5.0
    assert snapshot.tts_first_audio_to_first_play_audio_ms is not None


@pytest.mark.anyio
async def test_media_runner_cancels_unplayed_pre_response_ack_when_answer_arrives() -> None:
    transport = CapturingTransport()
    llm = DelayedFirstChunkLLMClient(response_text="The answer is ready.")
    tts = HangingPreResponseAckTextToSpeech(
        ack_text="One moment.",
        answer_text="The answer is ready.",
    )
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="hello"),
        llm=llm,
        tts=tts,
        transport=transport,
        turn_timeout_seconds=10,
        pre_response_ack_text="One moment.",
        pre_response_ack_delay_seconds=0.01,
    )

    try:
        turn_task = asyncio.create_task(runner.accept_audio(b"caller audio"))
        await asyncio.wait_for(tts.ack_started.wait(), timeout=0.5)
        assert transport.messages == []

        llm.release_first_chunk.set()
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)
        await asyncio.wait_for(turn_task, timeout=0.5)
    finally:
        await runner.close()

    assert tts.texts == ["One moment.", "The answer is ready."]
    assert tts.ack_cancelled.is_set()
    assert [message["event"] for message in transport.messages] == ["playAudio"]
    assert _decode_text_audio_payload(transport.messages[0]) == b"The answer is ready."


@pytest.mark.anyio
async def test_media_runner_skips_pre_response_ack_when_llm_first_chunk_is_fast() -> None:
    transport = CapturingTransport()
    tts = RecordingTextToSpeech()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="hello"),
        llm=RecordingLLMClient(response_text="The answer is ready."),
        tts=tts,
        transport=transport,
        pre_response_ack_text="One moment.",
        pre_response_ack_delay_seconds=1.0,
    )

    await runner.accept_audio(b"caller audio")
    await runner.close()

    assert tts.texts == ["The answer is ready."]


@pytest.mark.anyio
async def test_media_runner_falls_back_after_pre_response_ack_when_llm_hangs() -> None:
    transport = CapturingTransport()
    tts = RecordingTextToSpeech()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="hello"),
        llm=NeverReplyingLLMClient(),
        tts=tts,
        transport=transport,
        turn_timeout_seconds=0.05,
        pre_response_ack_text="One moment.",
        pre_response_ack_delay_seconds=0.01,
    )

    await asyncio.wait_for(runner.accept_audio(b"caller audio"), timeout=0.2)
    await runner.close()

    assert tts.texts == [
        "One moment.",
        "I'm sorry, I had trouble processing that. Please try again.",
    ]


@pytest.mark.anyio
async def test_media_runner_does_not_flush_unsafe_first_phrase_fragment() -> None:
    transport = CapturingTransport()
    llm = GatedSecondChunkLLMClient(first_chunk="The, ", second_chunk="answer is ready.")
    tts = RecordingTextToSpeech()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="hello"),
        llm=llm,
        tts=tts,
        transport=transport,
    )

    try:
        turn_task = asyncio.create_task(runner.accept_audio(b"caller audio"))
        await asyncio.wait_for(llm.first_chunk_yielded.wait(), timeout=0.5)
        await asyncio.sleep(0.05)
        assert tts.texts == []
        assert transport.messages == []
        assert not turn_task.done()

        llm.release_second_chunk.set()
        await asyncio.wait_for(turn_task, timeout=0.5)
    finally:
        await runner.close()

    assert tts.texts == ["The, answer is ready."]


@pytest.mark.anyio
async def test_media_runner_coalesces_ready_reply_chunks_after_first_synthesis() -> None:
    transport = CapturingTransport()
    tts = RecordingTextToSpeech()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="book appointment"),
        llm=RecordingLLMClient(
            response_text=(
                "Your appointment is confirmed. "
                "I found an opening tomorrow morning. "
                "Does that work for you?"
            )
        ),
        tts=tts,
        transport=transport,
    )

    await runner.accept_audio(b"caller audio")
    await runner.close()

    assert tts.texts == [
        "Your appointment is confirmed.",
        "I found an opening tomorrow morning. Does that work for you?",
    ]
    assert [_decode_text_audio_payload(message) for message in transport.messages] == [
        b"Your appointment is confirmed.",
        b"I found an opening tomorrow morning. Does that work for you?",
    ]


def test_looks_incomplete_classifies_pauses() -> None:
    from voice_agent.workflow.turn_policy import looks_incomplete

    assert looks_incomplete("umm")
    assert looks_incomplete("so")
    assert looks_incomplete("my number is 98")  # mid-number
    assert looks_incomplete("matlab")
    assert looks_incomplete("मेरा नंबर मतलब")
    assert looks_incomplete("मैं booking करना चाहता हूँ तो")
    assert looks_incomplete("मेरी appointment है लेकिन")
    assert not looks_incomplete("What is my balance?")
    assert not looks_incomplete("मेरा balance क्या है?")
    assert not looks_incomplete("मुंबई")
    assert not looks_incomplete("Mumbai")  # short complete answer
    assert not looks_incomplete("")


def test_split_complete_sentences_flushes_on_boundaries() -> None:
    from voice_agent.media.session import _split_complete_sentences

    sentences, remainder = _split_complete_sentences("Sure, I can help. What day")
    assert sentences == ["Sure, I can help."]
    assert remainder == " What day"

    # Decimals are not split mid-number while streaming.
    sentences, remainder = _split_complete_sentences("It costs 9.5 dollars")
    assert sentences == []
    assert remainder == "It costs 9.5 dollars"


def test_split_speakable_reply_chunks_flushes_conservative_first_phrases() -> None:
    from voice_agent.media.session import _split_speakable_reply_chunks

    chunks, remainder = _split_speakable_reply_chunks(
        "I can help with that, let me check",
        allow_phrase=True,
    )
    assert chunks == ["I can help with that,"]
    assert remainder == " let me check"

    chunks, remainder = _split_speakable_reply_chunks(
        "Your appointment is ready, let me check",
        allow_phrase=True,
    )
    assert chunks == ["Your appointment is ready,"]
    assert remainder == " let me check"

    chunks, remainder = _split_speakable_reply_chunks("The, answer is ready.", allow_phrase=True)
    assert chunks == []
    assert remainder == "The, answer is ready."

    chunks, remainder = _split_speakable_reply_chunks(
        "The, answer is still streaming",
        allow_phrase=True,
    )
    assert chunks == []
    assert remainder == "The, answer is still streaming"

    chunks, remainder = _split_speakable_reply_chunks("It costs 1,000 rupees", allow_phrase=True)
    assert chunks == []
    assert remainder == "It costs 1,000 rupees"

    chunks, remainder = _split_speakable_reply_chunks("I can help, really", allow_phrase=False)
    assert chunks == []
    assert remainder == "I can help, really"


def test_split_speakable_reply_chunks_soft_flushes_safe_first_phrase() -> None:
    from voice_agent.media.session import _split_speakable_reply_chunks

    chunks, remainder = _split_speakable_reply_chunks(
        "I can help with your appointment today let me check",
        allow_phrase=True,
    )
    assert chunks == ["I can help with your appointment"]
    assert remainder == " today let me check"

    chunks, remainder = _split_speakable_reply_chunks(
        "I can help with your appointment for",
        allow_phrase=True,
    )
    assert chunks == []
    assert remainder == "I can help with your appointment for"

    chunks, remainder = _split_speakable_reply_chunks(
        "The payable amount today is 1200",
        allow_phrase=True,
    )
    assert chunks == []
    assert remainder == "The payable amount today is 1200"


def test_split_speakable_reply_chunks_supports_luxtts_quality_profile() -> None:
    from voice_agent.media.session import ReplyChunkingConfig, _split_speakable_reply_chunks

    chunking = ReplyChunkingConfig(
        min_first_phrase_chars=36,
        min_soft_phrase_chars=42,
        max_soft_phrase_chars=96,
        max_early_phrase_chars=96,
    )

    chunks, remainder = _split_speakable_reply_chunks(
        "I can help with that, let me check",
        allow_phrase=True,
        chunking_config=chunking,
    )
    assert chunks == []
    assert remainder == "I can help with that, let me check"

    chunks, remainder = _split_speakable_reply_chunks(
        "I can help with your appointment today let me check the calendar.",
        allow_phrase=True,
        chunking_config=chunking,
    )
    assert chunks == ["I can help with your appointment today let me check"]
    assert remainder == " the calendar."


def test_split_speakable_reply_chunks_flushes_unicode_first_phrases() -> None:
    from voice_agent.media.session import _split_speakable_reply_chunks

    chunks, remainder = _split_speakable_reply_chunks(
        "मैं आपकी appointment देख सकता हूँ, कृपया बताइए",
        allow_phrase=True,
    )
    assert chunks == ["मैं आपकी appointment देख सकता हूँ,"]
    assert remainder == " कृपया बताइए"

    chunks, remainder = _split_speakable_reply_chunks(
        "भुगतान राशि 1200",
        allow_phrase=True,
    )
    assert chunks == []
    assert remainder == "भुगतान राशि 1200"


def test_split_speakable_reply_chunks_does_not_flush_common_hindi_acknowledgements() -> None:
    from voice_agent.media.session import _split_speakable_reply_chunks

    chunks, remainder = _split_speakable_reply_chunks(
        "ठीक है, मैं देखता हूँ",
        allow_phrase=True,
    )
    assert chunks == []
    assert remainder == "मैं देखता हूँ"

    chunks, remainder = _split_speakable_reply_chunks(
        "जी हाँ, मैं मदद कर सकता हूँ",
        allow_phrase=True,
    )
    assert chunks == []
    assert remainder == "मैं मदद कर सकता हूँ"


def test_split_speakable_reply_chunks_does_not_flush_safe_terminal_acknowledgement() -> None:
    from voice_agent.media.session import _split_speakable_reply_chunks

    chunks, remainder = _split_speakable_reply_chunks("Sure.", allow_phrase=True)
    assert chunks == []
    assert remainder == "Sure."

    chunks, remainder = _split_speakable_reply_chunks("ठीक है।", allow_phrase=True)
    assert chunks == []
    assert remainder == "ठीक है।"

    chunks, remainder = _split_speakable_reply_chunks("Dr.", allow_phrase=True)
    assert chunks == []
    assert remainder == "Dr."

    chunks, remainder = _split_speakable_reply_chunks("Amount is 1200.", allow_phrase=True)
    assert chunks == []
    assert remainder == "Amount is 1200."


def test_split_speakable_reply_chunks_does_not_flush_generic_acknowledgement_prefixes() -> None:
    from voice_agent.media.session import _split_speakable_reply_chunks

    chunks, remainder = _split_speakable_reply_chunks(
        "Sure, I can help with that.",
        allow_phrase=True,
    )
    assert chunks == []
    assert remainder == "I can help with that."

    chunks, remainder = _split_speakable_reply_chunks(
        "Sure. I can help with that.",
        allow_phrase=True,
    )
    assert chunks == []
    assert remainder == "I can help with that."

    chunks, remainder = _split_speakable_reply_chunks(
        "Of course, I can check that for you.",
        allow_phrase=True,
    )
    assert chunks == []
    assert remainder == "I can check that for you."


@pytest.mark.anyio
async def test_media_runner_ignores_single_frame_transient_during_playback() -> None:
    transport = CapturingTransport()
    stt = DelayedSpeechToText()
    tts = ControlledChunkedTextToSpeech()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=LocalLLMClient(response_text="Streaming reply."),
        tts=tts,
        transport=transport,
        barge_in_min_frames=2,
    )

    try:
        await runner.accept_audio(b"raw caller audio")
        await stt.emit_final("I need service")
        await tts.first_chunk_sent.wait()
        # A single loud frame (a click/cough) must NOT cut the agent off.
        await runner.accept_audio(_active_pcm_frame())
        await runner.accept_audio(b"\x00\x00" * 320)  # silence resets the streak
        tts.release_second_chunk.set()
    finally:
        await runner.close()

    events = [message["event"] for message in transport.messages]
    assert "clearAudio" not in events


@pytest.mark.anyio
async def test_media_runner_does_not_answer_a_bare_backchannel() -> None:
    transport = CapturingTransport()
    stt = ScriptedStreamingSpeechToText()
    llm = RecordingLLMClient(response_text="Okay.")
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=RecordingTextToSpeech(),
        transport=transport,
    )

    try:
        await runner.accept_audio(b"\x10\x00" * 200)
        # Bare acknowledgements should not trigger a turn.
        await stt.emit(TranscriptEvent(text="haan", is_final=True, speech_final=True))
        await stt.emit(TranscriptEvent(text="okay", is_final=True, speech_final=True))
        await stt.emit(TranscriptEvent(text="ठीक है", is_final=True, speech_final=True))
        await stt.emit(TranscriptEvent(text="जी हाँ", is_final=True, speech_final=True))
        await asyncio.sleep(0.05)
        assert llm.user_texts == []
        # A real request still gets answered.
        await stt.emit(
            TranscriptEvent(text="I want to book an appointment.", is_final=True, speech_final=True)
        )
        await asyncio.wait_for(_wait_for_messages(transport, count=1), timeout=0.5)
    finally:
        await runner.close()

    assert llm.user_texts == ["I want to book an appointment."]


@pytest.mark.anyio
async def test_media_runner_answers_first_backchannel_after_greeting() -> None:
    transport = CapturingTransport()
    stt = ScriptedStreamingSpeechToText()
    llm = RecordingLLMClient(response_text="I can hear you clearly.")
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=stt,
        llm=llm,
        tts=RecordingTextToSpeech(),
        transport=transport,
        greeting="Hi, can you hear me clearly?",
    )

    try:
        await runner.greet()
        assert runner._turn_task is not None
        await runner._turn_task
        await runner.accept_audio(b"\x10\x00" * 200)
        await stt.emit(TranscriptEvent(text="yes", is_final=True, speech_final=True))
        await asyncio.wait_for(_wait_for_messages(transport, count=2), timeout=0.5)
    finally:
        await runner.close()

    assert llm.user_texts == ["yes"]


def test_is_backchannel_classifies_acknowledgements() -> None:
    from voice_agent.workflow.turn_policy import is_backchannel

    assert is_backchannel("haan")
    assert is_backchannel("okay")
    assert is_backchannel("mhm")
    assert is_backchannel("haan haan")
    assert is_backchannel("yeah okay")
    assert is_backchannel("ठीक है")
    assert is_backchannel("जी हाँ")
    assert not is_backchannel("yes please book it")
    assert not is_backchannel("जी हाँ please book it")
    assert not is_backchannel("I want to reschedule")
    assert not is_backchannel("")


@pytest.mark.anyio
async def test_media_runner_emits_error_outcome_when_llm_fails() -> None:
    transport = CapturingTransport()
    snapshots: list[VoiceTurnLatencySnapshot] = []
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=LocalSpeechToText(trigger_phrase="hello"),
        llm=FailingLLMClient(),
        tts=TextEncodingSpeech(),
        transport=transport,
        latency_observer=snapshots.append,
    )

    await runner.accept_audio(b"hello")
    await runner.close()

    # A turn that fails before any answer audio used to emit no telemetry at all.
    assert len(snapshots) == 1
    assert snapshots[0].outcome == "error"


@pytest.mark.anyio
async def test_media_runner_emits_timeout_outcome_when_turn_times_out() -> None:
    transport = CapturingTransport()
    snapshots: list[VoiceTurnLatencySnapshot] = []
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=LocalSpeechToText(trigger_phrase="hello"),
        llm=NeverReplyingLLMClient(),
        tts=TextEncodingSpeech(),
        transport=transport,
        turn_timeout_seconds=0.01,
        latency_observer=snapshots.append,
    )

    await asyncio.wait_for(runner.accept_audio(b"hello"), timeout=0.2)
    await runner.close()

    assert len(snapshots) == 1
    assert snapshots[0].outcome == "timeout"


@pytest.mark.anyio
async def test_media_runner_emits_reply_too_large_outcome() -> None:
    transport = CapturingTransport()
    snapshots: list[VoiceTurnLatencySnapshot] = []
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=SingleTranscriptSpeechToText(text="caller request"),
        llm=RecordingLLMClient(response_text="x" * 9),
        tts=RecordingTextToSpeech(),
        transport=transport,
        max_reply_chars=8,
        latency_observer=snapshots.append,
    )

    await runner.accept_audio(b"caller audio")
    await runner.close()

    assert len(snapshots) == 1
    assert snapshots[0].outcome == "reply_too_large"


@pytest.mark.anyio
async def test_media_runner_ends_call_when_turn_budget_exhausted() -> None:
    transport = CapturingTransport()
    snapshots: list[VoiceTurnLatencySnapshot] = []
    hangups: list[str] = []
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=LocalSpeechToText(trigger_phrase="hello"),
        llm=LocalLLMClient(response_text="Ready."),
        tts=StaticAudioTextToSpeech(b"\x01\x00"),
        transport=transport,
        max_turns_per_call=1,
        latency_observer=snapshots.append,
        hangup_observer=hangups.append,
    )

    await runner.accept_audio(b"hello")  # turn 1: handled normally
    await runner.accept_audio(b"hello")  # turn 2: exceeds the budget -> end the call
    await runner.close()

    assert hangups == ["call-1"]
    assert snapshots[0].outcome == "success"
    assert snapshots[-1].outcome == "budget_exceeded"


@pytest.mark.anyio
async def test_media_runner_sends_checkpoint_after_answer_when_enabled() -> None:
    transport = CapturingTransport()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=LocalSpeechToText(trigger_phrase="hello"),
        llm=LocalLLMClient(response_text="Ready."),
        tts=StaticAudioTextToSpeech(b"\x01\x00"),
        transport=transport,
        playback_checkpoints=True,
    )

    await runner.accept_audio(b"hello")
    await runner.close()

    events = [message["event"] for message in transport.messages]
    assert "playAudio" in events
    checkpoints = [m for m in transport.messages if m["event"] == "checkpoint"]
    assert checkpoints
    assert checkpoints[-1]["name"] == runner._last_checkpoint_name


@pytest.mark.anyio
async def test_media_runner_omits_checkpoint_by_default() -> None:
    transport = CapturingTransport()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=LocalSpeechToText(trigger_phrase="hello"),
        llm=LocalLLMClient(response_text="Ready."),
        tts=StaticAudioTextToSpeech(b"\x01\x00"),
        transport=transport,
    )

    await runner.accept_audio(b"hello")
    await runner.close()

    assert "checkpoint" not in [message["event"] for message in transport.messages]


@pytest.mark.anyio
async def test_notify_played_stream_brings_playback_window_forward() -> None:
    transport = CapturingTransport()
    runner = MediaSessionRunner(
        call_id="call-1",
        stream_id="stream-1",
        audio_format=PlivoAudioFormat.L16_16KHZ,
        stt=LocalSpeechToText(trigger_phrase="hello"),
        llm=LocalLLMClient(response_text="Ready."),
        tts=StaticAudioTextToSpeech(b"\x01\x00\x02\x00\x03\x00\x04\x00"),
        transport=transport,
        playback_checkpoints=True,
    )

    await runner.accept_audio(b"hello")
    name = runner._last_checkpoint_name
    before = runner._playback_active_until_at
    assert before is not None

    # A non-matching ack is ignored; the matching one ends the local estimate early.
    runner.notify_played_stream("does-not-match")
    assert runner._playback_active_until_at == before
    runner.notify_played_stream(name)
    assert runner._playback_active_until_at is not None
    assert runner._playback_active_until_at <= before

    await runner.close()
