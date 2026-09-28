"""`BriefingService`: the intake's `BriefingBuilder` port (M1-05) and the doctor route's builder.

build (fixed templates) -> check (fail closed) -> summarise (optional, checked) -> store in
`briefings` -> write the FHIR document Bundle (conditional create). The briefing is served even
when the document cannot be written: its outcome is recorded next to it. A document the EHR did
not take is written later by the write-back consumer (`ensure_document`, M2-12), with the
triage's retries. The API runs as one process per clinic set, so the service is shared by the
graph and the route.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from careflow_contracts.api_models import Briefing
from careflow_intake.briefing import Summarizer, make_briefing
from careflow_intake.state import IntakeState
from careflow_integration.fhir.client import FhirClient
from careflow_integration.fhir.writeback.composition import DocumentOutcome, write_document
from careflow_integration.fhir.writeback.ports import WritebackStatus, WritebackStore

logger = logging.getLogger("careflow_api.briefing")


class BriefingStore(Protocol):
    async def get(self, clinic_id: uuid.UUID, session_id: uuid.UUID) -> dict[str, Any] | None:
        """The stored briefing JSON, or None."""
        ...

    async def save(
        self,
        clinic_id: uuid.UUID,
        session_id: uuid.UUID,
        briefing: Briefing,
        *,
        source_missing: int,
    ) -> None: ...

    async def set_document(
        self, clinic_id: uuid.UUID, session_id: uuid.UUID, outcome: DocumentOutcome
    ) -> None: ...

    async def document(
        self, clinic_id: uuid.UUID, session_id: uuid.UUID
    ) -> tuple[str | None, str | None]:
        """The FHIR document's status and Bundle id, or (None, None) without a briefing."""
        ...


class StateSource(Protocol):
    async def load(self, clinic_id: str, session_id: str) -> IntakeState | None:
        """The session's intake state (the graph's checkpoint), or None."""
        ...


class NoStateSource:
    """Until M1-12 runs the intake in the API there is no session state to build from."""

    async def load(self, clinic_id: str, session_id: str) -> IntakeState | None:
        return None


class BriefingService:
    def __init__(
        self,
        *,
        store: BriefingStore,
        writebacks: WritebackStore,
        fhir: Callable[[], FhirClient],
        summarizer: Summarizer | None,
        states: StateSource | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.writebacks = writebacks
        self.fhir = fhir
        self.summarizer = summarizer
        self.states = states or NoStateSource()
        self.clock = clock
        self._tasks: set[asyncio.Task[None]] = set()

    async def make(self, state: IntakeState) -> Briefing:
        """Build, check, summarise and store the briefing, then write its FHIR document."""
        briefing, _ = await self._make(state)
        return briefing

    async def ensure_document(self, state: IntakeState) -> DocumentOutcome:
        """The briefing's FHIR document, written now if it is not yet (M2-12). The write-back
        consumer calls it once the triage is in the EHR: a document that an EHR outage stopped
        at the nurse's decision is then written, and a failure is retried with the triage's."""
        await self.drain()  # a briefing still being built writes its own document first
        clinic_id, session_id = uuid.UUID(state["clinic_id"]), uuid.UUID(state["session_id"])
        status, bundle_id = await self.store.document(clinic_id, session_id)
        if status == WritebackStatus.ok.value:
            return DocumentOutcome(WritebackStatus.ok, bundle_id=bundle_id)
        stored = await self.store.get(clinic_id, session_id)
        if stored is None:
            _, outcome = await self._make(state)
            return outcome
        outcome = await self.document(state, Briefing.model_validate(stored))
        await self.store.set_document(clinic_id, session_id, outcome)
        logger.info(
            "briefing.document_retried",
            extra={"session_id": state["session_id"], "document_status": outcome.status.value},
        )
        return outcome

    async def _make(self, state: IntakeState) -> tuple[Briefing, DocumentOutcome]:
        result = await make_briefing(state, now=self.clock(), summarizer=self.summarizer)
        briefing = result.briefing
        clinic_id, session_id = uuid.UUID(state["clinic_id"]), uuid.UUID(state["session_id"])
        await self.store.save(clinic_id, session_id, briefing, source_missing=len(result.problems))
        outcome = await self.document(state, briefing)
        await self.store.set_document(clinic_id, session_id, outcome)
        logger.info(
            "briefing.built",
            extra={
                "session_id": state["session_id"],
                "summary_status": briefing.summary_status.value,
                "source_missing": len(result.problems),
                "document_status": outcome.status.value,
            },
        )
        return briefing, outcome

    async def document(self, state: IntakeState, briefing: Briefing) -> DocumentOutcome:
        written = await self.writebacks.resources(state["clinic_id"], state["session_id"])
        client = self.fhir()
        try:
            return await write_document(
                client, briefing, patient_ref=state.get("patient_ref"), written=written
            )
        except Exception as exc:  # fail safe: the briefing is served without its document
            logger.warning("briefing.document_failed", extra={"error": type(exc).__name__})
            return DocumentOutcome(WritebackStatus.failed, error=type(exc).__name__)
        finally:
            await client.aclose()

    async def build(self, state: IntakeState) -> str:
        """The `BriefingBuilder` port. The briefing (with its model summary) is built in the
        background, so the nurse's decision returns as soon as the patient is queued and the
        triage is written back; a doctor who opens it first gets it built on demand."""
        task = asyncio.create_task(self._make_logged(state), name="briefing")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return f"briefing:{state['session_id']}"

    async def _make_logged(self, state: IntakeState) -> None:
        try:
            await self.make(state)
        except Exception:  # the doctor route builds it again on demand
            logger.exception("briefing.build_failed", extra={"session_id": state["session_id"]})

    async def drain(self) -> None:
        """Wait for briefings still being built (shutdown and tests)."""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


__all__ = ["BriefingService", "BriefingStore", "NoStateSource", "StateSource"]
