import asyncio
from typing import Any

from voice_agent.app import _build_call_transcript_observer, _DebouncedTranscriptWriter


class RecordingSessionStore:
    def __init__(self) -> None:
        self.transcripts: list[str | None] = []

    async def upsert(self, update: Any) -> dict[str, Any]:
        self.transcripts.append(update.transcript)
        return {}

    async def reserve_idempotency_key(self, update: Any) -> dict[str, Any] | None:
        del update
        return None

    async def get(self, identifier: str) -> dict[str, Any] | None:
        del identifier
        return None

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


async def test_debounced_transcript_writer_coalesces_bursts() -> None:
    store = RecordingSessionStore()
    writer = _DebouncedTranscriptWriter(store, call_id="call-1", interval_seconds=0.05)

    # A burst of turns within one interval collapses to a single write of the latest value.
    writer.submit("a")
    writer.submit("b")
    writer.submit("c")
    await asyncio.sleep(0.01)  # let the leading flush run
    assert store.transcripts == ["c"]

    # Subsequent updates wait out the interval, then the trailing flush writes the latest.
    writer.submit("d")
    writer.submit("e")
    await asyncio.sleep(0.12)
    assert store.transcripts == ["c", "e"]


async def test_transcript_observer_persists_immediately_when_interval_is_zero() -> None:
    store = RecordingSessionStore()
    observe = _build_call_transcript_observer(store, call_id="call-1", interval_seconds=0.0)

    observe("first")
    observe("second")
    await asyncio.sleep(0.01)

    assert store.transcripts == ["first", "second"]
