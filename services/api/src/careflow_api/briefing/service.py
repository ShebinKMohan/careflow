"""`BriefingService`: the intake's `BriefingBuilder` port (M1-05) and the doctor route's builder.

build (fixed templates) -> check (fail closed) -> summarise (optional, checked) -> store in
`briefings` -> write the FHIR document Bundle (conditional create). The briefing is served even
when the document cannot be written: its outcome is recorded next to it. The API runs as one
process per clinic set, so the service is shared by the graph and the route.
"""

from __future__ import annotations

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

    async def make(self, state: IntakeState) -> Briefing:
        """Build, check, summarise and store the briefing, then write its FHIR document."""
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
        return briefing

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
        """The `BriefingBuilder` port: returns the briefing's reference."""
        briefing = await self.make(state)
        return f"briefing:{briefing.session_id}"


__all__ = ["BriefingService", "BriefingStore", "NoStateSource", "StateSource"]
