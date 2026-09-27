"""The intake graph's ports (M1-05) implemented by the API (M1-12).

- `CheckinIdentifier`: the patient identifier the check-in route found.
- `CachedEhrReader`: the M1-08 FHIR reader, with the check-in's lookup reused by the graph.
- `TriageUrgency` / `NoTriageModel`: the M1-04 urgency model, or no suggestion when none is
  installed (the nurse then sets the level; never a fake level).
- `OutboxEvents`: `EventSink` over the M1-11 transactional outbox.
- `QueuePort`: `QueueService` over the M1-10 queue.
- `WritebackPort`: the M1-09 write-back, run when the nurse's decision resumes the graph.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy import update

from careflow_api.events.outbox import OutboxPublisher
from careflow_api.events.publisher import Event
from careflow_api.models import PatientSession
from careflow_api.queue.service import QueueService as ApiQueue
from careflow_api.rls import clinic_session
from careflow_api.writeback_store import SqlWritebackStore
from careflow_contracts.generated.ehr_fetch_schema import EhrFetch, PatientIdentifier
from careflow_contracts.generated.events.envelope_schema import EventType
from careflow_contracts.generated.events.triage_confirmed_schema import TriageConfirmedData
from careflow_contracts.generated.structured_facts_schema import FactKey, StructuredFacts
from careflow_contracts.generated.urgency_suggestion_schema import TriageScale, UrgencySuggestion
from careflow_intake.ports import Assignment, NoSuggestion
from careflow_intake.state import IntakeState, structured_facts
from careflow_integration.fhir.reader import FhirEhrReader
from careflow_integration.fhir.writeback.handler import WritebackHandler
from careflow_integration.fhir.writeback.ports import WritebackOutcome, WritebackStatus
from careflow_integration.fhir.writeback.record import News2Summary, TriageRecord
from careflow_rules.news2 import news2_inputs, score_news2

logger = logging.getLogger("careflow_api.intake")

EHR_CACHE_SECONDS = 120.0


class CheckinIdentifier:
    """The identifier the check-in route put in the initial state (None: anonymous)."""

    async def identify(self, state: IntakeState) -> PatientIdentifier | None:
        return state.get("patient_identifier")


@dataclass
class CachedEhrReader:
    """The FHIR reader; a lookup made at check-in is reused by the graph's first node."""

    reader: FhirEhrReader = field(default_factory=FhirEhrReader)
    ttl_seconds: float = EHR_CACHE_SECONDS
    _cache: dict[tuple[str, str, str], tuple[float, EhrFetch]] = field(default_factory=dict)

    async def fetch_context(self, identifier: PatientIdentifier, clinic_id: str) -> EhrFetch:
        key = (clinic_id, identifier.system, identifier.value)
        found = self._cache.get(key)
        if found is not None and time.monotonic() - found[0] < self.ttl_seconds:
            return found[1]
        fetch = await self.reader.fetch_context(identifier, clinic_id)
        self._cache[key] = (time.monotonic(), fetch)
        return fetch


class NoTriageModel:
    """No model is installed: never a suggestion, so the nurse sets every level."""

    model_version = "none"

    def predict(
        self,
        facts: StructuredFacts,
        *,
        scale: TriageScale = TriageScale.KTAS,
        now: datetime | None = None,
    ) -> UrgencySuggestion:
        raise NoSuggestion("no triage model is installed")


class TriageUrgency:
    """The trained urgency model (M1-04). A patient outside its data gets no suggestion."""

    def __init__(self, model: Any) -> None:
        self.model = model

    @property
    def model_version(self) -> str:
        return str(self.model.model_version)

    @classmethod
    def load(cls, path: Path) -> TriageUrgency | NoTriageModel:
        if not (path / "model.joblib").exists():
            logger.warning(
                "intake.port", extra={"port": "urgency", "impl": "none", "path": str(path)}
            )
            return NoTriageModel()
        from careflow_triage.inference import UrgencyModel

        return cls(UrgencyModel.load(path))

    def predict(
        self,
        facts: StructuredFacts,
        *,
        scale: TriageScale = TriageScale.KTAS,
        now: datetime | None = None,
    ) -> UrgencySuggestion:
        from careflow_triage.inference import OutOfScopeError

        try:
            suggestion: UrgencySuggestion = self.model.predict(facts, scale=scale, now=now)
        except OutOfScopeError as exc:
            raise NoSuggestion(str(exc)) from exc
        return suggestion


@dataclass
class OutboxEvents:
    """`EventSink`: every event goes through the transactional outbox (M1-11); the same
    idempotency key is one event."""

    publisher: OutboxPublisher = field(default_factory=OutboxPublisher)
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    async def emit(
        self, event_type: EventType | str, data: BaseModel, idempotency_key: str
    ) -> None:
        clinic_id = str(data.model_dump(mode="json")["clinic_id"])
        event = Event(
            type=str(getattr(event_type, "value", event_type)),
            data=data,
            timestamp=self.clock(),
            idempotency_key=idempotency_key,
        )
        async with clinic_session(clinic_id) as db:
            await self.publisher.add(db, event)

    async def publish(self, event_type: str, data: BaseModel) -> None:
        """The write-back's `EventPublisher` (internal events)."""
        await self.emit(
            event_type, data, f"{data.model_dump(mode='json').get('session_id')}:{event_type}"
        )


@dataclass
class QueuePort:
    """`QueueService`: a token at the nurse's level (M1-10); the session is then queued."""

    queue: ApiQueue

    async def assign(self, *, session_id: str, clinic_id: str, level: int) -> Assignment:
        clinic = uuid.UUID(clinic_id)
        async with clinic_session(clinic) as db:
            assignment = await self.queue.assign_token(
                db, clinic_id=clinic, session_id=session_id, level=level
            )
            await db.execute(
                update(PatientSession)
                .where(PatientSession.id == uuid.UUID(session_id))
                .values(status="queued")
            )
        await self.queue.broadcast(clinic, assignment.department)  # the new token's place (M2-07)
        return Assignment(token=assignment.token_code, room=assignment.room_hint)


def completed_actions(state: IntakeState) -> list[str]:
    """Required actions already done: the ECG, once the nurse recorded it."""
    done = any(entry.key is FactKey.tasks_ecg_done_at for entry in state.get("facts", []))
    return ["ecg_within_10_min"] if done else []


def triage_record(state: IntakeState) -> TriageRecord:
    """Everything one write-back needs, from the graph's state after the nurse's decision."""
    decision = state.get("nurse_decision")
    if decision is None:
        raise ValueError("no nurse decision to write back")
    facts = structured_facts(state)
    score = score_news2(news2_inputs(facts))
    news2 = (
        News2Summary(
            total=score.total,
            partial=score.partial,
            missing=list(score.missing),
            scored_at=decision.decided_at,
        )
        if score is not None
        else None
    )
    return TriageRecord(
        clinic_id=state["clinic_id"],
        session_id=state["session_id"],
        patient_ref=state.get("patient_ref"),
        arrived_at=state["arrived_at"],
        decision=decision,
        facts=facts,
        alerts=list(state.get("alerts", [])),
        news2=news2,
        completed_actions=completed_actions(state),
    )


@dataclass
class _Record:
    record: TriageRecord

    async def load(self, clinic_id: str, session_id: str) -> TriageRecord | None:
        return self.record


@dataclass
class WritebackPort:
    """`Writeback`: the confirmed triage to the hospital's FHIR server (M1-09), synchronously, so
    the briefing's document can reference what was written. Never raises: the status says."""

    events: OutboxEvents
    store: SqlWritebackStore = field(default_factory=SqlWritebackStore)

    async def write(self, state: IntakeState) -> str:
        return (await self.write_outcome(state)).status.value

    async def write_outcome(self, state: IntakeState) -> WritebackOutcome:
        """`write`, with the whole outcome: the bus consumer needs to know whether a failed write
        may still succeed (`retryable`: the EHR was unreachable) or never will."""
        try:
            record = triage_record(state)
        except ValueError as exc:  # a validation error's text can quote facts: log the type only
            error = f"no triage record ({type(exc).__name__})"
            logger.warning("intake.writeback", extra={"error": error})
            return WritebackOutcome(
                str(state.get("clinic_id") or ""),
                str(state.get("session_id") or ""),
                WritebackStatus.failed,
                0,
                error=error,
            )
        decision = record.decision
        event = TriageConfirmedData(
            clinic_id=record.clinic_id,
            session_id=record.session_id,
            decision_id=decision.decision_id,
            suggestion_id=decision.suggestion_id,
            action=decision.action,
            final_level=decision.final_level,
            scale=decision.scale,
            override_reason_code=decision.override_reason_code,
            nurse_user_id=decision.nurse_user_id,
            decided_at=decision.decided_at,
        )
        handler = WritebackHandler(records=_Record(record), store=self.store, events=self.events)
        try:
            return await handler.on_triage_confirmed(event)
        finally:
            await handler.client.aclose()


__all__ = [
    "CachedEhrReader",
    "CheckinIdentifier",
    "NoTriageModel",
    "OutboxEvents",
    "QueuePort",
    "TriageUrgency",
    "WritebackPort",
    "completed_actions",
    "triage_record",
]
