"""Deterministic stand-ins for every port.

They make the graph runnable without an LLM, an EHR or a database: tests use them, and they
stay the fallback until M1-06/M1-07/M1-12 inject real adapters. Nothing here gives medical
advice; the stub rule and the stub suggestion exist only to exercise the graph.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from pydantic import BaseModel

from careflow_contracts.generated.alert_schema import Alert, AlertSeverity, RuleId
from careflow_contracts.generated.events.envelope_schema import EventType
from careflow_contracts.generated.fact_schema import TextFact, TriState, TriStateFact
from careflow_contracts.generated.structured_facts_schema import (
    EhrContext,
    FactKey,
    StructuredFacts,
    Turn,
    TurnRole,
)
from careflow_contracts.generated.urgency_suggestion_schema import TriageScale, UrgencySuggestion
from careflow_contracts.validators import iter_facts
from careflow_intake.facts import FactEntry, fact_class
from careflow_intake.ports import (
    Assignment,
    EhrUnavailable,
    ExtractionResult,
    IntakePorts,
    NoSuggestion,
    Question,
    RulesOutcome,
)
from careflow_intake.state import IntakeState

CROCKFORD: Final = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
START: Final = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)


def stable_fact_id(*parts: str) -> str:
    """A contract fact id (`f_` + 26 Crockford base32 characters) derived from `parts`."""
    number = int.from_bytes(hashlib.sha256("|".join(parts).encode()).digest()[:17], "big")
    chars = []
    for _ in range(26):
        chars.append(CROCKFORD[number & 31])
        number >>= 5
    return "f_" + "".join(reversed(chars))


def _asked(state: IntakeState) -> int:
    return sum(1 for turn in state.get("turns", []) if turn.role is TurnRole.assistant)


class SteppingClock:
    """Starts at `start` and moves `step` forward on every call."""

    def __init__(self, start: datetime = START, step: timedelta = timedelta(seconds=1)) -> None:
        self.now = start
        self.step = step

    def __call__(self) -> datetime:
        now = self.now
        self.now += self.step
        return now


@dataclass
class StubIdentifier:
    patient_ref: str | None = "Patient/synthetic-0001"

    async def identify(self, state: IntakeState) -> str | None:
        return self.patient_ref


@dataclass
class StubEhrReader:
    context: EhrContext | None = None
    down: bool = False

    async def fetch_context(self, patient_ref: str) -> EhrContext | None:
        if self.down:
            raise EhrUnavailable("the stub EHR is down")
        return self.context


DEFAULT_QUESTIONS: Final = (
    Question(
        question_id="q_complaint",
        text="What brings you in today?",
        key=FactKey.chief_complaint,
    ),
    Question(
        question_id="q_chest_pain",
        text="Do you have chest pain?",
        key=FactKey.chest_pain_present,
    ),
    Question(
        question_id="q_fever",
        text="Have you had a fever?",
        key=FactKey.infection_fever_reported,
    ),
)


@dataclass
class ScriptedConversation:
    """Asks `questions` in order, then completes."""

    questions: Sequence[Question] = DEFAULT_QUESTIONS

    async def next_question(self, state: IntakeState) -> Question | None:
        asked = _asked(state)
        return self.questions[asked] if asked < len(self.questions) else None


class EndlessConversation:
    """Never completes: the graph's caps must stop it."""

    async def next_question(self, state: IntakeState) -> Question | None:
        number = _asked(state) + 1
        return Question(question_id=f"q_{number:03d}", text=f"Tell me more ({number}).")


@dataclass
class AnswerExtractor:
    """Turns the latest patient answer to a keyed question into one PATIENT_STATED fact:
    yes/no/unsure for a tri-state key, the text for a text key; nothing otherwise. The fact id
    comes from the turn (id and time) and key, so extracting again replaces it."""

    incomplete: bool = False
    fail: bool = False

    async def extract(
        self,
        turns: Sequence[Turn],
        existing_facts: Sequence[FactEntry],
        question_keys: Mapping[str, FactKey],
    ) -> ExtractionResult:
        if self.fail:
            raise RuntimeError("the stub extractor failed")
        answers = [turn for turn in turns if turn.role is TurnRole.patient]
        turn = answers[-1] if answers else None
        key = question_keys.get(turn.turn_id) if turn is not None else None
        value = _value(key, turn.text) if turn is not None and key is not None else None
        if turn is None or key is None or value is None:
            return ExtractionResult(extraction_incomplete=self.incomplete, provider="stub")
        fact = {
            "fact_id": stable_fact_id(turn.turn_id, turn.created_at.isoformat(), key.value),
            "provenance": "PATIENT_STATED",
            "source": {
                "kind": "transcript_span",
                "turn_id": turn.turn_id,
                "start": 0,
                "end": len(turn.text),
                "quote": turn.text[:1000],
            },
            "recorded_at": turn.created_at,
            "value": value,
        }
        entry = FactEntry.model_validate({"key": key, "fact": fact})
        return ExtractionResult(
            facts=[entry], extraction_incomplete=self.incomplete, provider="stub"
        )


def _value(key: FactKey, text: str) -> str | None:
    cls = fact_class(key)
    if issubclass(cls, TriStateFact):
        word = text.strip().lower()
        if word.startswith("y"):
            return TriState.yes.value
        if word.startswith("n"):
            return TriState.no.value
        return TriState.unknown.value
    if issubclass(cls, TextFact):
        return text[:2000]
    return None


@dataclass
class RecordingRulesRunner:
    """Records every StructuredFacts it is given. Its one stub rule raises
    CHEST_PAIN_ECG_10MIN when `chest_pain.present` is yes."""

    seen: list[StructuredFacts] = field(default_factory=list)

    def run(
        self, facts: StructuredFacts, *, now: datetime, arrived_at: datetime | None
    ) -> RulesOutcome:
        self.seen.append(facts)
        present = facts.chest_pain.present if facts.chest_pain else None
        if present is None or present.value is not TriState.yes:
            return RulesOutcome()
        alert = Alert(
            alert_id=f"alr_{facts.session_id}_cp"[:64],
            session_id=facts.session_id,
            rule_id=RuleId.CHEST_PAIN_ECG_10MIN,
            ruleset_version="0.0.0-stub",
            severity=AlertSeverity.critical,
            evidence_fact_ids=[present.fact_id],
            required_action="ecg_within_10_min",
            partial=False,
            raised_at=now,
            deadline_at=(arrived_at or now) + timedelta(minutes=10),
        )
        return RulesOutcome(alerts=[alert])


@dataclass
class StubUrgencyModel:
    """Always suggests `level` (citing up to three session facts) unless `unavailable` is set."""

    level: int = 3
    confidence: float = 0.7
    unavailable: str | None = None
    calls: int = 0

    def predict(
        self,
        facts: StructuredFacts,
        *,
        scale: TriageScale = TriageScale.KTAS,
        now: datetime | None = None,
    ) -> UrgencySuggestion:
        self.calls += 1
        if self.unavailable is not None:
            raise NoSuggestion(self.unavailable)
        fact_ids = [fact.fact_id for _, fact in iter_facts(facts)][:3]
        reasons = [
            {"text": f"Stub reason {i + 1}", "fact_ids": [fid]} for i, fid in enumerate(fact_ids)
        ]
        return UrgencySuggestion.model_validate(
            {
                "suggestion_id": f"sug_{facts.session_id}"[:64],
                "session_id": facts.session_id,
                "level": self.level,
                "scale": scale,
                "display_label": f"{scale.value} {self.level}",
                "confidence": self.confidence,
                "reasons": reasons or [{"text": "Stub suggestion without facts", "fact_ids": []}],
                "model_id": "stub-urgency",
                "model_version": "stub-1",
                "created_at": now or START,
            }
        )


@dataclass
class StubQueue:
    assigned: list[tuple[str, int]] = field(default_factory=list)

    async def assign(self, *, session_id: str, clinic_id: str, level: int) -> Assignment:
        self.assigned.append((session_id, level))
        return Assignment(token=f"A{len(self.assigned):03d}", room="triage-1")


@dataclass
class StubWriteback:
    written: list[str] = field(default_factory=list)

    async def write(self, state: IntakeState) -> str:
        self.written.append(state["session_id"])
        return "stubbed"


class StubBriefing:
    async def build(self, state: IntakeState) -> str:
        return f"briefing:{state['session_id']}"


@dataclass
class RecordingEventSink:
    """Keeps the first event per idempotency key and counts every attempt."""

    events: list[tuple[EventType, BaseModel, str]] = field(default_factory=list)
    attempts: int = 0

    async def emit(self, event_type: EventType, data: BaseModel, idempotency_key: str) -> None:
        self.attempts += 1
        if any(key == idempotency_key for _, _, key in self.events):
            return
        self.events.append((event_type, data, idempotency_key))

    @property
    def types(self) -> list[str]:
        return [event_type.value for event_type, _, _ in self.events]


def stub_ports(**overrides: Any) -> IntakePorts:
    """IntakePorts with every port stubbed; pass keyword arguments to replace some."""
    ports: dict[str, Any] = {
        "identifier": StubIdentifier(),
        "ehr": StubEhrReader(),
        "conversation": ScriptedConversation(),
        "extractor": AnswerExtractor(),
        "rules": RecordingRulesRunner(),
        "urgency": StubUrgencyModel(),
        "queue": StubQueue(),
        "writeback": StubWriteback(),
        "briefing": StubBriefing(),
        "events": RecordingEventSink(),
        "clock": SteppingClock(),
    }
    ports.update(overrides)
    return IntakePorts(**ports)


__all__ = [
    "DEFAULT_QUESTIONS",
    "START",
    "AnswerExtractor",
    "EndlessConversation",
    "RecordingEventSink",
    "RecordingRulesRunner",
    "ScriptedConversation",
    "SteppingClock",
    "StubBriefing",
    "StubEhrReader",
    "StubIdentifier",
    "StubQueue",
    "StubUrgencyModel",
    "StubWriteback",
    "stable_fact_id",
    "stub_ports",
]
