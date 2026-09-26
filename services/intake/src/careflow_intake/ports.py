"""The interfaces the intake graph calls. Every node talks to the world only through these ports,
so the graph can run on deterministic stubs (`careflow_intake.stubs`) and M1-07/M1-12 can inject
real adapters.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from careflow_contracts.generated.alert_schema import Alert
from careflow_contracts.generated.ehr_fetch_schema import EhrFetch, PatientIdentifier
from careflow_contracts.generated.events.envelope_schema import EventType
from careflow_contracts.generated.structured_facts_schema import (
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
    source: Literal["llm", "template"] = "template"  # who phrased the text (M1-07)
    # Shown before the question, e.g. the fixed nurse-coming message after a critical alert.
    notice: Annotated[str, Field(min_length=1, max_length=500)] | None = None


class ExtractionResult(BaseModel):
    """Facts extracted from the transcript. `extraction_incomplete` (a failed or partial
    extraction) is shown to the nurse; the rules still run on what is known."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    facts: list[FactEntry] = []
    dropped_count: int = 0
    extraction_incomplete: bool = False
    error_code: str | None = None
    prompt_version: str | None = None
    model_id: str | None = None
    provider: str | None = None


class RulesOutcome(BaseModel):
    """What the rules engine found on the structured facts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    alerts: list[Alert] = []
    needed_facts: list[NeededFact] = []


class Assignment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    token: OpaqueId
    room: Annotated[str, Field(min_length=1, max_length=64)]


class NoSuggestion(Exception):
    """The urgency model cannot suggest a level (e.g. a patient outside its training data);
    the nurse sets the level without a suggestion."""


class Identifier(Protocol):
    async def identify(self, state: IntakeState) -> PatientIdentifier | None:
        """How the patient checked in (keypad id, ID card, QR), or None when anonymous."""
        ...


class EhrReader(Protocol):
    async def fetch_context(self, identifier: PatientIdentifier, clinic_id: str) -> EhrFetch:
        """The patient's EHR context (M1-08). An EHR problem is reported in `status`
        (partial, unavailable, not_found, ambiguous), not raised."""
        ...


class Conversation(Protocol):
    async def next_question(self, state: IntakeState) -> Question | None:
        """The next question, or None when the conversation is complete."""
        ...


class Extractor(Protocol):
    async def extract(
        self,
        turns: Sequence[Turn],
        existing_facts: Sequence[FactEntry],
        question_keys: Mapping[str, FactKey],
    ) -> ExtractionResult:
        """PATIENT_STATED facts from the transcript. `question_keys` maps each patient turn id to
        the fact its question asked about. Fact ids should be stable for the same statement, so
        extracting again replaces a fact instead of duplicating it."""
        ...


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
    "EventSink",
    "ExtractionResult",
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
