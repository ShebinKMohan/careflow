"""The interfaces the intake graph calls. Every node talks to the world only through these ports,
so the graph can run on deterministic stubs (`careflow_intake.stubs`) and M1-07/M1-12 can inject
real adapters.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field

from careflow_contracts.generated.alert_schema import Alert
from careflow_contracts.generated.events.envelope_schema import EventType
from careflow_contracts.generated.structured_facts_schema import (
    EhrContext,
    FactKey,
    NeededFact,
    StructuredFacts,
    Turn,
)
from careflow_contracts.generated.urgency_suggestion_schema import TriageScale, UrgencySuggestion
from careflow_intake.facts import FactEntry

if TYPE_CHECKING:
    from careflow_intake.state import IntakeState

type OpaqueId = Annotated[
    str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
]


class Question(BaseModel):
    """What the kiosk asks the patient next."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question_id: OpaqueId
    text: Annotated[str, Field(min_length=1, max_length=500)]
    key: FactKey | None = None


class Extraction(BaseModel):
    """Facts extracted from one patient turn. `complete=False` flags `extraction_incomplete`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    facts: list[FactEntry] = []
    complete: bool = True


class RulesOutcome(BaseModel):
    """What the rules engine found on the structured facts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    alerts: list[Alert] = []
    needed_facts: list[NeededFact] = []


class Assignment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    token: OpaqueId
    room: Annotated[str, Field(min_length=1, max_length=64)]


class EhrUnavailable(Exception):
    """The EHR could not be read; the intake continues with `ehr_unavailable`."""


class NoSuggestion(Exception):
    """The urgency model cannot suggest a level (e.g. a patient outside its training data);
    the nurse sets the level without a suggestion."""


class Identifier(Protocol):
    async def identify(self, state: IntakeState) -> str | None:
        """The patient reference (FHIR `Patient/...`), or None when unknown."""
        ...


class EhrReader(Protocol):
    async def fetch_context(self, patient_ref: str) -> EhrContext | None:
        """Allergies, conditions and medications; raises `EhrUnavailable`."""
        ...


class Conversation(Protocol):
    async def next_question(self, state: IntakeState) -> Question | None:
        """The next question, or None when the conversation is complete."""
        ...


class Extractor(Protocol):
    async def extract(self, state: IntakeState, turn: Turn) -> Extraction: ...


class RulesRunner(Protocol):
    def run(
        self, facts: StructuredFacts, *, now: datetime, arrived_at: datetime | None
    ) -> RulesOutcome:
        """Deterministic safety rules on the structured facts (safety rule 1)."""
        ...


class UrgencyModel(Protocol):
    """The M1-04 port: `careflow_triage.inference.UrgencyModel.predict` has this signature."""

    def predict(
        self,
        facts: StructuredFacts,
        *,
        scale: TriageScale = TriageScale.KTAS,
        now: datetime | None = None,
    ) -> UrgencySuggestion:
        """A suggestion only; raises `NoSuggestion` when the model cannot suggest."""
        ...


class QueueService(Protocol):
    async def assign(self, *, session_id: str, clinic_id: str, level: int) -> Assignment: ...


class Writeback(Protocol):
    async def write(self, state: IntakeState) -> str:
        """Write the encounter back to the EHR; returns a status."""
        ...


class BriefingBuilder(Protocol):
    async def build(self, state: IntakeState) -> str:
        """Build the doctor briefing; returns its reference."""
        ...


class EventSink(Protocol):
    async def emit(self, event_type: EventType, data: BaseModel, idempotency_key: str) -> None:
        """Publish an event's `data`. A repeated idempotency key must not publish twice."""
        ...


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class IntakePorts:
    identifier: Identifier
    ehr: EhrReader
    conversation: Conversation
    extractor: Extractor
    rules: RulesRunner
    urgency: UrgencyModel
    queue: QueueService
    writeback: Writeback
    briefing: BriefingBuilder
    events: EventSink
    clock: Callable[[], datetime] = field(default=utc_now)


__all__ = [
    "Assignment",
    "BriefingBuilder",
    "Conversation",
    "EhrReader",
    "EhrUnavailable",
    "EventSink",
    "Extraction",
    "Extractor",
    "Identifier",
    "IntakePorts",
    "NoSuggestion",
    "QueueService",
    "Question",
    "RulesOutcome",
    "RulesRunner",
    "UrgencyModel",
    "Writeback",
    "utc_now",
]
