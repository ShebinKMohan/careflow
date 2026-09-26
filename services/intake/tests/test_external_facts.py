"""Facts from outside the conversation (device and nurse vitals) enter with `aupdate_state` and
are seen by the next `run_rules`."""

from __future__ import annotations

from typing import Any

from careflow_contracts.generated.structured_facts_schema import FactKey
from careflow_intake.checkpoint import memory_checkpointer
from careflow_intake.facts import FactEntry
from careflow_intake.graph import add_facts, answer, build_graph, pending, start_intake
from careflow_intake.stubs import START, RecordingRulesRunner, stable_fact_id


def spo2(value: float, *, fact_id: str, provenance: str = "DEVICE_MEASURED") -> dict[str, Any]:
    source = (
        {"kind": "device", "device_id": "dev_oximeter_1", "reading_id": "rd_0001"}
        if provenance == "DEVICE_MEASURED"
        else {"kind": "nurse", "staff_user_id": "nurse_001"}
    )
    return {
        "key": "vitals.spo2",
        "fact": {
            "fact_id": fact_id,
            "provenance": provenance,
            "source": source,
            "recorded_at": "2026-09-27T09:00:30Z",
            "value": value,
            "unit": "%",
        },
    }


async def test_a_device_reading_added_while_waiting_is_seen_by_the_next_run_rules(
    make_ports: Any, script: dict[str, Any]
) -> None:
    rules = RecordingRulesRunner()
    graph = build_graph(make_ports(rules=rules), memory_checkpointer())
    sid = script["session_id"]
    await start_intake(graph, session_id=sid, clinic_id=script["clinic_id"], arrived_at=START)
    reading = stable_fact_id(sid, "spo2")
    await add_facts(graph, sid, [spo2(89, fact_id=reading)])
    assert rules.seen == []  # nothing has run yet: the graph is still waiting for the patient
    result = await answer(graph, sid, script["answers"][0])
    assert (pending(result) or {})["kind"] == "patient_input"  # the conversation goes on
    vitals = rules.seen[-1].vitals
    assert vitals is not None and vitals.spo2 is not None
    assert vitals.spo2.value == 89 and vitals.spo2.fact_id == reading


async def test_the_same_fact_id_replaces_instead_of_duplicating(
    make_ports: Any, script: dict[str, Any]
) -> None:
    rules = RecordingRulesRunner()
    graph = build_graph(make_ports(rules=rules), memory_checkpointer())
    sid = script["session_id"]
    await start_intake(graph, session_id=sid, clinic_id=script["clinic_id"], arrived_at=START)
    reading = stable_fact_id(sid, "spo2")
    await add_facts(graph, sid, [spo2(92, fact_id=reading)])
    await add_facts(graph, sid, [spo2(88, fact_id=reading)])
    result = await answer(graph, sid, script["answers"][0])
    readings = [entry for entry in result["facts"] if entry.key is FactKey.vitals_spo2]
    assert len(readings) == 1 and isinstance(readings[0], FactEntry)
    assert rules.seen[-1].vitals is not None and rules.seen[-1].vitals.spo2 is not None
    assert rules.seen[-1].vitals.spo2.value == 88


async def test_a_nurse_value_wins_over_a_newer_device_reading(
    make_ports: Any, script: dict[str, Any]
) -> None:
    rules = RecordingRulesRunner()
    graph = build_graph(make_ports(rules=rules), memory_checkpointer())
    sid = script["session_id"]
    await start_intake(graph, session_id=sid, clinic_id=script["clinic_id"], arrived_at=START)
    nurse = spo2(95, fact_id=stable_fact_id(sid, "nurse"), provenance="NURSE_CONFIRMED")
    device = spo2(90, fact_id=stable_fact_id(sid, "device"))
    device["fact"]["recorded_at"] = "2026-09-27T09:01:00Z"  # newer than the nurse's value
    await add_facts(graph, sid, [nurse, device])
    await answer(graph, sid, script["answers"][0])
    vitals = rules.seen[-1].vitals
    assert vitals is not None and vitals.spo2 is not None and vitals.spo2.value == 95
