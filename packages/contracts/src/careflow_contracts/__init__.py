"""Source of truth for CareFlow schemas: OpenAPI 3.1, JSON Schemas and the FHIR mapping table.

Every lane imports these types from `careflow_contracts` and never redefines them. The models in
`careflow_contracts.generated` come from `packages/contracts/schemas` via `make contracts`.
"""

from typing import TypeAlias

from careflow_contracts.alert_text import render_display_text, required_action_for
from careflow_contracts.generated.alert_schema import Alert, AlertSeverity, RuleId
from careflow_contracts.generated.fact_schema import (
    CodeFact,
    Coding,
    DeviceSource,
    FactBase,
    FhirSource,
    IntegerFact,
    ModelSource,
    NumberFact,
    NurseSource,
    Provenance,
    TextFact,
    TranscriptSpanSource,
    TriState,
    TriStateFact,
)
from careflow_contracts.generated.nurse_decision_schema import (
    DecisionAction,
    NurseDecision,
    OverrideReasonCode,
)
from careflow_contracts.generated.structured_facts_schema import (
    AcvpuCode,
    AcvpuCoding,
    AcvpuFact,
    AgeMonthsFact,
    AgeYearsFact,
    BloodPressureFact,
    ChestPain,
    ChildAmber,
    ChildDanger,
    EhrContext,
    ExtractionStatus,
    FactKey,
    Fast,
    HeartRateFact,
    Infection,
    Language,
    NeededFact,
    NeededFactAudience,
    PainScoreFact,
    Patient,
    RespiratoryRateFact,
    Spo2Fact,
    StructuredFacts,
    Symptom,
    SymptomSeverity,
    TemperatureFact,
    Turn,
    TurnRole,
    Vitals,
)
from careflow_contracts.generated.urgency_suggestion_schema import (
    SuggestionReason,
    TriageScale,
    UrgencySuggestion,
)

SourceRef: TypeAlias = TranscriptSpanSource | FhirSource | DeviceSource | NurseSource | ModelSource  # noqa: UP040
"""Where exactly a fact came from; discriminated by `kind`."""

__version__ = "0.0.0"

__all__ = [
    "AcvpuCode",
    "AcvpuCoding",
    "AcvpuFact",
    "AgeMonthsFact",
    "AgeYearsFact",
    "Alert",
    "AlertSeverity",
    "BloodPressureFact",
    "ChestPain",
    "ChildAmber",
    "ChildDanger",
    "CodeFact",
    "Coding",
    "DecisionAction",
    "DeviceSource",
    "EhrContext",
    "ExtractionStatus",
    "FactBase",
    "FactKey",
    "Fast",
    "FhirSource",
    "HeartRateFact",
    "Infection",
    "IntegerFact",
    "Language",
    "ModelSource",
    "NeededFact",
    "NeededFactAudience",
    "NumberFact",
    "NurseDecision",
    "NurseSource",
    "OverrideReasonCode",
    "PainScoreFact",
    "Patient",
    "Provenance",
    "RespiratoryRateFact",
    "RuleId",
    "SourceRef",
    "Spo2Fact",
    "StructuredFacts",
    "SuggestionReason",
    "Symptom",
    "SymptomSeverity",
    "TemperatureFact",
    "TextFact",
    "TranscriptSpanSource",
    "TriState",
    "TriStateFact",
    "TriageScale",
    "Turn",
    "TurnRole",
    "UrgencySuggestion",
    "Vitals",
    "render_display_text",
    "required_action_for",
]
