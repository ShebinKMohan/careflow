"""identify and fetch_ehr_context."""

from __future__ import annotations

import logging

from careflow_intake.nodes import Update
from careflow_intake.ports import IntakePorts
from careflow_intake.state import IntakeSettings, IntakeState

logger = logging.getLogger(__name__)


async def identify(state: IntakeState, ports: IntakePorts, settings: IntakeSettings) -> Update:
    """Resolve the patient reference and start the intake clock."""
    patient_ref = await ports.identifier.identify(state)
    return {
        "patient_ref": patient_ref,
        "started_at": ports.clock(),
        "turn_count": 0,
        "conversation_complete": False,
        "node_trace": ["identify"],
    }


async def fetch_ehr_context(
    state: IntakeState, ports: IntakePorts, settings: IntakeSettings
) -> Update:
    """Allergies, conditions and medications. Without them the intake continues and the nurse
    sees `ehr_unavailable` (also when the patient is not identified)."""
    patient_ref = state.get("patient_ref")
    unavailable: Update = {
        "ehr_context": None,
        "flags": {"ehr_unavailable": True},
        "node_trace": ["fetch_ehr_context"],
    }
    if patient_ref is None:
        return unavailable
    try:
        context = await ports.ehr.fetch_context(patient_ref)
    except Exception as exc:  # fail safe: the EHR is optional for the intake
        logger.warning(
            "EHR context unavailable for %s: %s", state["session_id"], type(exc).__name__
        )
        return unavailable
    return {"ehr_context": context, "node_trace": ["fetch_ehr_context"]}
