"""assign_token, writeback and build_briefing: after the nurse's decision."""

from __future__ import annotations

from careflow_contracts.generated.events.envelope_schema import EventType
from careflow_contracts.generated.events.intake_completed_schema import IntakeCompletedData
from careflow_contracts.generated.structured_facts_schema import ExtractionStatus
from careflow_intake.nodes import Update
from careflow_intake.ports import IntakePorts
from careflow_intake.state import IntakeSettings, IntakeState


async def assign_token(state: IntakeState, ports: IntakePorts, settings: IntakeSettings) -> Update:
    """Queue the patient at the level the nurse decided."""
    decision = state.get("nurse_decision")
    if decision is None:
        raise RuntimeError("assign_token needs the nurse's decision")
    assignment = await ports.queue.assign(
        session_id=state["session_id"], clinic_id=state["clinic_id"], level=decision.final_level
    )
    return {"token": assignment.token, "room": assignment.room, "node_trace": ["assign_token"]}


async def writeback(state: IntakeState, ports: IntakePorts, settings: IntakeSettings) -> Update:
    return {"writeback_status": await ports.writeback.write(state), "node_trace": ["writeback"]}


async def build_briefing(
    state: IntakeState, ports: IntakePorts, settings: IntakeSettings
) -> Update:
    """Build the doctor briefing and publish `intake.completed`."""
    briefing_ref = await ports.briefing.build(state)
    incomplete = state.get("flags", {}).get("extraction_incomplete", False)
    data = IntakeCompletedData(
        clinic_id=state["clinic_id"],
        session_id=state["session_id"],
        extraction_status=ExtractionStatus.incomplete if incomplete else ExtractionStatus.complete,
        completed_at=ports.clock(),
    )
    key = f"{state['session_id']}:intake.completed"
    await ports.events.emit(EventType.intake_completed, data, key)
    return {"briefing_ref": briefing_ref, "node_trace": ["build_briefing"]}
