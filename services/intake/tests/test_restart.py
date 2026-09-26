"""The state survives a restart: pause at the nurse review, close the saver, then resume the same
thread on a new graph and a new saver over the same Postgres database."""

from __future__ import annotations

from typing import Any

from careflow_contracts.generated.alert_schema import Alert
from careflow_contracts.generated.urgency_suggestion_schema import UrgencySuggestion
from careflow_intake.checkpoint import postgres_checkpointer
from careflow_intake.facts import FactEntry
from careflow_intake.graph import (
    answer,
    build_graph,
    decide,
    pending,
    start_intake,
    thread_config,
    waiting_at,
)
from careflow_intake.stubs import START, RecordingEventSink, StubQueue


async def test_resume_after_restart(
    postgres_url: str, make_ports: Any, script: dict[str, Any], make_decision: Any
) -> None:
    sid = script["session_id"]
    async with postgres_checkpointer(postgres_url) as saver:
        before = make_ports()
        graph = build_graph(before, saver)
        result = await start_intake(
            graph, session_id=sid, clinic_id=script["clinic_id"], arrived_at=START
        )
        for text in script["answers"]:
            result = await answer(graph, sid, text)
        assert (pending(result) or {})["kind"] == "nurse_review"
        suggestion = result["suggestion"]
    # The first saver's connection is closed. A new process would start from here.

    async with postgres_checkpointer(postgres_url) as saver:
        after = make_ports()
        graph = build_graph(after, saver)
        snapshot = await graph.aget_state(thread_config(sid))
        assert waiting_at(snapshot, "nurse_review")
        restored = snapshot.values
        assert all(isinstance(entry, FactEntry) for entry in restored["facts"])
        assert isinstance(restored["alerts"][0], Alert)
        assert restored["suggestion"] == suggestion and isinstance(suggestion, UrgencySuggestion)
        done = await decide(graph, sid, make_decision(sid, suggestion))

    assert pending(done) is None and done["token"] == "A001"
    assert done["node_trace"][-4:] == [
        "nurse_review",
        "assign_token",
        "writeback",
        "build_briefing",
    ]
    # Only the nodes after the restart ran on the new ports.
    assert isinstance(after.queue, StubQueue) and after.queue.assigned == [(sid, suggestion.level)]
    assert isinstance(after.events, RecordingEventSink)
    assert after.events.types == ["triage.confirmed", "intake.completed"]
    assert isinstance(before.events, RecordingEventSink)
    assert before.events.types == ["alert.raised", "triage.suggested"]
