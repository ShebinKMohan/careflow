import pytest

from voice_agent.control_plane import (
    IngestingCallEventStore,
    _event_to_ingest_payload,
    _to_ingest_payload,
)
from voice_agent.storage.call_sessions import CallSessionUpdate


def test_ingest_payload_includes_structured_transcript() -> None:
    transcript = '[{"role":"user","text":"hello","turn":1}]'

    payload = _to_ingest_payload(
        CallSessionUpdate(call_id="call-1", status="completed", transcript=transcript)
    )

    assert payload is not None
    assert payload["transcript"] == transcript


def test_ingest_payload_allows_transcript_only_update() -> None:
    transcript = '[{"role":"user","text":"hello","turn":1}]'

    payload = _to_ingest_payload(CallSessionUpdate(call_id="call-1", transcript=transcript))

    assert payload == {
        "provider_call_id": "call-1",
        "event": "transcript_updated",
        "transcript": transcript,
    }


def test_event_ingest_payload_maps_latency_event_to_call_timeline_update() -> None:
    payload = _event_to_ingest_payload(
        {
            "event": "voice_turn_latency",
            "call_id": "call-1",
            "stream_id": "stream-1",
            "turn_epoch": 1,
            "turn_commit_source": "google_live_input_transcription",
            "response_plan_action": "google_live_realtime",
            "turn_committed_to_first_play_audio_ms": 123.4,
        }
    )

    assert payload == {
        "provider_call_id": "call-1",
        "event": "voice_turn_latency",
        "last_event": {
            "event": "voice_turn_latency",
            "call_id": "call-1",
            "stream_id": "stream-1",
            "turn_epoch": 1,
            "turn_commit_source": "google_live_input_transcription",
            "response_plan_action": "google_live_realtime",
            "turn_committed_to_first_play_audio_ms": 123.4,
        },
    }


@pytest.mark.anyio
async def test_ingesting_call_event_store_mirrors_latency_events_to_control_plane() -> None:
    inner = RecordingCallEventStore()
    client = RecordingControlPlaneClient()
    store = IngestingCallEventStore(inner, client)
    event = {"event": "voice_turn_latency", "call_id": "call-1", "turn_epoch": 1}

    await store.record(event)

    assert inner.events == [event]
    assert client.events == [event]


class RecordingCallEventStore:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def record(self, event: dict[str, object]) -> None:
        self.events.append(event)

    async def recent(self, *, count: int) -> list[dict[str, object]]:
        return list(reversed(self.events[-count:]))

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


class RecordingControlPlaneClient:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def fire_event(self, event: dict[str, object]) -> None:
        self.events.append(event)
