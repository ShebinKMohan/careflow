"""New vitals refresh pending adult adaptive triage using only current measured facts.

The real LangGraph runtime uses its memory checkpointer; no database or model weights are used.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from careflow_api.intake.runtime import IntakeRuntime
from careflow_api.routers.sessions import device_facts
from careflow_contracts.api_models import DeviceVitalsReading
from careflow_contracts.generated.events.envelope_schema import EventType
from careflow_contracts.generated.nurse_decision_schema import NurseDecision
from careflow_contracts.generated.structured_facts_schema import FactKey, Language, StructuredFacts
from careflow_contracts.generated.urgency_suggestion_schema import TriageScale, UrgencySuggestion
from careflow_intake.checkpoint import memory_checkpointer
from careflow_intake.facts import FactEntry
from careflow_intake.graph import build_graph
from careflow_intake.nodes.triage import InvalidNurseDecision
from careflow_intake.state import IntakeState, structured_facts
from careflow_intake.stubs import START, RecordingEventSink, StubUrgencyModel, stub_ports

FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "packages/contracts/fixtures/valid/chest_pain.structured-facts.json"
    ).read_text()
)


class Model(StubUrgencyModel):
    def __init__(self) -> None:
        super().__init__()
        self.seen: list[StructuredFacts] = []
        self.failed = False

    def predict(
        self, facts: StructuredFacts, *, scale: TriageScale = TriageScale.KTAS, now: Any = None
    ) -> UrgencySuggestion:
        self.seen.append(facts)
        if self.failed:
            raise RuntimeError("Synthetic model failure")
        result = super().predict(facts, scale=scale, now=now)
        return result.model_copy(update={"suggestion_id": f"sug_refresh_{self.calls}"})


def vitals(reading: str = "reading_1", *, minutes: int = 1) -> list[FactEntry]:
    return device_facts(
        "ses_refresh",
        DeviceVitalsReading.model_validate(
            {
                "device_id": "device_test",
                "reading_id": reading,
                "measured_at": START + timedelta(minutes=minutes),
                "heart_rate": 118,
                "spo2": 93,
                "temperature_c": 38.2,
            }
        ),
    )


async def start(
    *, mode: str = "adaptive", child: bool = False
) -> tuple[IntakeRuntime, Model, RecordingEventSink]:
    model, events = Model(), RecordingEventSink()
    ports = stub_ports(urgency=model, events=events)
    runtime = IntakeRuntime(build_graph(ports, memory_checkpointer()), ports)
    age = {**FIXTURE["patient"]["age_years"], "value": 8 if child else 58}
    state: IntakeState = {
        "session_id": "ses_refresh",
        "clinic_id": "clinic_demo",
        "language": Language.en,
        "arrived_at": START,
        "patient_type": "child" if child else "adult",
        "reception_context": {"intake_mode": mode},
        "conversation_complete": True,
        "facts": [FactEntry.model_validate({"key": "patient.age_years", "fact": age})],
    }
    await runtime.start_reception(state)
    return runtime, model, events


async def test_new_device_vitals_refresh_model_after_rules_and_alerts() -> None:
    runtime, model, events = await start()
    before = await runtime.state("ses_refresh")
    assert before is not None
    old = before.get("suggestion")
    assert old is not None
    old_id = old.suggestion_id
    model.level = 5  # A lower model estimate must not erase the independent safety alert.
    chest = FactEntry.model_validate(
        {
            "key": FactKey.chest_pain_present,
            "fact": FIXTURE["chest_pain"]["present"],
        }
    )
    result = await runtime.add_facts_and_evaluate("ses_refresh", [*vitals(), chest])
    assert len(model.seen) == 2
    measured = model.seen[-1].vitals
    assert measured is not None
    assert measured.heart_rate is not None and measured.heart_rate.value == 118
    assert measured.spo2 is not None and measured.spo2.value == 93
    assert measured.temperature_c is not None and measured.temperature_c.value == 38.2
    assert measured.systolic_bp is None and measured.respiratory_rate is None
    assert measured.acvpu is None and measured.diastolic_bp is None
    suggestion = result.get("suggestion")
    assert suggestion is not None and suggestion.suggestion_id != old_id
    assert suggestion.level == 5
    assert result.get("alerts") and result.get("nurse_decision") is None
    assert events.types[-2:] == ["alert.raised", "triage.suggested"]
    assert result.get("node_trace", [])[-2:] == ["run_rules", "suggest_urgency"]
    assert events.events[-1][1].model_dump()["suggestion_id"] == suggestion.suggestion_id
    view = await runtime.view("ses_refresh")
    assert view is not None and view.waiting == "nurse_review"


async def test_model_failure_removes_stale_suggestion_and_keeps_alerts() -> None:
    runtime, model, events = await start()
    model.failed = True
    chest = FactEntry.model_validate(
        {
            "key": FactKey.chest_pain_present,
            "fact": FIXTURE["chest_pain"]["present"],
        }
    )
    result = await runtime.add_facts_and_evaluate("ses_refresh", [*vitals(), chest])
    assert result.get("suggestion") is None
    assert "RuntimeError" in (result.get("suggestion_unavailable") or "")
    assert result.get("alerts") and events.types[-1] == "alert.raised"
    measured = structured_facts(result).vitals
    assert measured is not None and measured.respiratory_rate is None


@pytest.mark.parametrize(("mode", "child"), [("registration", False), ("adaptive", True)])
async def test_child_and_registration_only_visits_never_refresh_model(
    mode: str, child: bool
) -> None:
    runtime, model, _ = await start(mode=mode, child=child)
    result = await runtime.add_facts_and_evaluate("ses_refresh", vitals())
    assert model.seen == [] and result.get("suggestion") is None


async def test_duplicate_reading_and_empty_retry_do_not_repeat_prediction() -> None:
    runtime, model, _ = await start()
    reading = vitals()
    await runtime.add_facts_and_evaluate("ses_refresh", reading)
    await runtime.add_facts_and_evaluate("ses_refresh", reading)
    await runtime.add_facts_and_evaluate("ses_refresh", [])
    assert len(model.seen) == 2


async def test_new_suggestion_rejects_old_decision_and_stops_refresh_after_confirmation() -> None:
    runtime, model, events = await start()
    before = await runtime.state("ses_refresh")
    assert before is not None
    old = before.get("suggestion")
    assert old is not None
    result = await runtime.add_facts_and_evaluate("ses_refresh", vitals())
    decision = {
        "decision_id": "dec_refresh",
        "session_id": "ses_refresh",
        "suggestion_id": old.suggestion_id,
        "action": "CONFIRM",
        "final_level": 3,
        "scale": "KTAS",
        "nurse_user_id": "nurse_test",
        "decided_at": START + timedelta(minutes=2),
    }
    with pytest.raises(InvalidNurseDecision):
        await runtime.resume_nurse("ses_refresh", NurseDecision.model_validate(decision))
    current = result.get("suggestion")
    assert current is not None
    decision["suggestion_id"] = current.suggestion_id
    await runtime.resume_nurse("ses_refresh", NurseDecision.model_validate(decision))
    assert EventType.triage_confirmed.value in events.types
    after = await runtime.add_facts_and_evaluate("ses_refresh", vitals("reading_2", minutes=3))
    assert len(model.seen) == 2
    assert after.get("nurse_decision") is not None


async def test_vitals_during_patient_questions_wait_for_normal_triage_path() -> None:
    runtime, model, _ = await start()
    await runtime.graph.aupdate_state(
        {"configurable": {"thread_id": "ses_refresh"}},
        {"conversation_complete": False},
        as_node="ask",
    )
    await runtime.add_facts_and_evaluate("ses_refresh", vitals())
    assert len(model.seen) == 1
