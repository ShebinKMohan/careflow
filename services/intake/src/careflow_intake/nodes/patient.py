"""identify and fetch_ehr_context."""

from __future__ import annotations

import logging
from datetime import datetime

from careflow_contracts.generated.ehr_fetch_schema import EhrFetch, EhrStatus
from careflow_contracts.generated.structured_facts_schema import FactKey, Patient
from careflow_intake.facts import FactEntry
from careflow_intake.nodes import Update
from careflow_intake.ports import IntakePorts
from careflow_intake.state import IntakeSettings, IntakeState

logger = logging.getLogger(__name__)

USABLE = frozenset({EhrStatus.ok, EhrStatus.partial})


async def identify(state: IntakeState, ports: IntakePorts, settings: IntakeSettings) -> Update:
    """Resolve how the patient checked in, and start the intake clock."""
    identifier = await ports.identifier.identify(state)
    return {
        "patient_identifier": identifier,
        "started_at": ports.clock(),
        "turn_count": 0,
        "conversation_complete": False,
        "node_trace": ["identify"],
    }


def patient_facts(patient: Patient | None) -> list[FactEntry]:
    """The EHR demographics (age, sex) as facts, with their EHR provenance."""
    if patient is None:
        return []
    found = (
        (FactKey.patient_age_years, patient.age_years),
        (FactKey.patient_age_months, patient.age_months),
        (FactKey.patient_sex, patient.sex),
    )
    return [FactEntry(key=key, fact=fact) for key, fact in found if fact is not None]


def _unavailable(now: datetime, reason: str) -> EhrFetch:
    return EhrFetch(status=EhrStatus.unavailable, fetched_at=now, errors=[reason])


async def fetch_ehr_context(
    state: IntakeState, ports: IntakePorts, settings: IntakeSettings
) -> Update:
    """The EHR context (M1-08). Without it the intake goes on: the nurse sees the EHR status and
    `ehr_unavailable`, and the allergy question is asked fresh. An empty record is never shown
    as if it were complete."""
    identifier = state.get("patient_identifier")
    if identifier is None:
        fetch = EhrFetch(
            status=EhrStatus.not_found, fetched_at=ports.clock(), errors=["no patient identifier"]
        )
    else:
        try:
            fetch = await ports.ehr.fetch_context(identifier, state["clinic_id"])
        except Exception as exc:  # fail safe: the EHR is optional for the intake
            logger.warning("EHR reader failed for %s: %s", state["session_id"], type(exc).__name__)
            fetch = _unavailable(ports.clock(), f"reader failed ({type(exc).__name__})")
    update: Update = {
        "ehr_status": fetch.status,
        "ehr_errors": list(fetch.errors),
        "appointments": list(fetch.appointments),
        "node_trace": ["fetch_ehr_context"],
    }
    if fetch.status in USABLE:
        update |= {
            "patient_ref": fetch.patient_ref,
            "ehr_context": fetch.context,
            "facts": patient_facts(fetch.patient),
        }
    else:
        update |= {"ehr_context": None, "flags": {"ehr_unavailable": True}}
    return update
