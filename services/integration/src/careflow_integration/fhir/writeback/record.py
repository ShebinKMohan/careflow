"""`TriageRecord`: everything one write-back needs, loaded after the nurse's decision (M1-09)."""

from __future__ import annotations

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from careflow_contracts.generated.alert_schema import Alert
from careflow_contracts.generated.fact_schema import Identifier
from careflow_contracts.generated.nurse_decision_schema import NurseDecision
from careflow_contracts.generated.structured_facts_schema import FactKey, StructuredFacts


class News2Summary(BaseModel):
    """The NEWS2 the rules engine scored for this triage (`careflow_rules.News2Result`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    total: int = Field(ge=0, le=20)
    partial: bool
    missing: list[FactKey] = Field(default_factory=list)
    scored_at: AwareDatetime


class TriageRecord(BaseModel):
    """One confirmed triage. Only the nurse's final level is written, never a model suggestion
    (safety rule 3). `completed_actions` lists required actions already done (for example
    `ecg_within_10_min`), whose Tasks are then written as completed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    clinic_id: Identifier
    session_id: Identifier
    patient_ref: str | None = Field(default=None, pattern=r"^Patient/[A-Za-z0-9\-.]{1,64}$")
    arrived_at: AwareDatetime
    decision: NurseDecision
    facts: StructuredFacts
    alerts: list[Alert] = Field(default_factory=list)
    news2: News2Summary | None = None
    completed_actions: list[str] = Field(default_factory=list)


__all__ = ["News2Summary", "TriageRecord"]
