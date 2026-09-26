"""Outbound event types (Standard Webhooks envelope) and their data models.

The same envelope frames webhooks (M4-03) and WebSocket messages (M1-11). Event data carries
`clinic_id` and `session_id` and never a name, a date of birth or free text.

Internal events (`INTERNAL_DATA_MODELS`) travel only on the CareFlow event bus, e.g. to the audit
log: they are never a webhook or a WebSocket message, so they are not `EventType`s.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel

from careflow_contracts.generated.events.alert_raised_schema import AlertRaisedData
from careflow_contracts.generated.events.envelope_schema import EventEnvelope, EventType
from careflow_contracts.generated.events.fhir_write_completed_schema import FhirWriteCompletedData
from careflow_contracts.generated.events.intake_completed_schema import IntakeCompletedData
from careflow_contracts.generated.events.nurse_flag_schema import NurseFlagData
from careflow_contracts.generated.events.patient_feels_worse_schema import PatientFeelsWorseData
from careflow_contracts.generated.events.token_called_schema import TokenCalledData
from careflow_contracts.generated.events.token_no_show_schema import TokenNoShowData
from careflow_contracts.generated.events.triage_confirmed_schema import TriageConfirmedData
from careflow_contracts.generated.events.triage_suggested_schema import TriageSuggestedData

DATA_MODEL_FOR_EVENT: Final[dict[EventType, type[BaseModel]]] = {
    EventType.alert_raised: AlertRaisedData,
    EventType.triage_suggested: TriageSuggestedData,
    EventType.triage_confirmed: TriageConfirmedData,
    EventType.token_called: TokenCalledData,
    EventType.token_no_show: TokenNoShowData,
    EventType.intake_completed: IntakeCompletedData,
    EventType.patient_feels_worse: PatientFeelsWorseData,
    EventType.nurse_flag: NurseFlagData,
}

#: Internal event type -> data model. Bus only: never a webhook or a WebSocket message.
INTERNAL_DATA_MODELS: Final[dict[str, type[BaseModel]]] = {
    "fhir.write.completed": FhirWriteCompletedData,
}

__all__ = [
    "DATA_MODEL_FOR_EVENT",
    "INTERNAL_DATA_MODELS",
    "AlertRaisedData",
    "EventEnvelope",
    "EventType",
    "FhirWriteCompletedData",
    "IntakeCompletedData",
    "NurseFlagData",
    "PatientFeelsWorseData",
    "TokenCalledData",
    "TokenNoShowData",
    "TriageConfirmedData",
    "TriageSuggestedData",
]
