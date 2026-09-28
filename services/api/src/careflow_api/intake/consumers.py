"""Bus consumers for the intake (M1-12).

`fhir-writeback` (M1-09): on `triage.confirmed`, write the session's triage back to the EHR. The
graph already writes it when the nurse decides, so this is the at-least-once safety net (after a
restart, or while the EHR was down); every write is a conditional create, so a second one stores
nothing new. A failed write is never acknowledged (ADR-043): when the EHR was unreachable the event
stays pending and is retried until the write succeeds; when the EHR rejected it (or no Bundle
could be built) it goes to the dead-letter stream with the reason.

Once the triage is in the EHR, the briefing's FHIR document follows it (M2-12): the graph writes
the document when the nurse decides, and an EHR outage then left it unwritten for good. The
consumer now writes a document that is not in the EHR yet, and holds the event on the same terms:
pending while the EHR does not answer, dead-lettered when it rejects the document.
"""

from __future__ import annotations

import logging
from typing import Any, Final, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from careflow_api.events.bus import BusEvent, Handler, PermanentFailure, TransientFailure
from careflow_api.intake.ports import WritebackPort
from careflow_api.intake.runtime import IntakeRuntime
from careflow_integration.fhir.writeback.composition import DocumentOutcome
from careflow_integration.fhir.writeback.ports import WritebackStatus

logger = logging.getLogger("careflow_api.intake")

WRITEBACK_GROUP: Final = "fhir-writeback"


class WritebackUnavailable(TransientFailure):
    """The EHR did not answer the write (or answered 429/502/503/504): retry later."""


class WritebackRejected(PermanentFailure):
    """The EHR rejected the write, or no Bundle could be built: dead-letter it with the reason."""


class DocumentWriter(Protocol):
    async def ensure_document(self, state: Any) -> DocumentOutcome:
        """The briefing's FHIR document, written if it is not yet (`BriefingService`)."""
        ...


def writeback_consumer(
    runtime: IntakeRuntime, writeback: WritebackPort, briefings: DocumentWriter | None = None
) -> Handler:
    async def handle(db: AsyncSession, event: BusEvent) -> None:
        if event.type != "triage.confirmed" or event.session_id is None:
            return
        # The session lock waits for the graph's own step (decision, write-back, briefing), so the
        # two never post the same Bundle at once (HAPI answers a concurrent twin with 409).
        async with runtime.serialized(event.session_id):
            done = await writeback.store.status(str(event.clinic_id), event.session_id)
            if done is WritebackStatus.ok and briefings is None:
                return
            state = await runtime.state(event.session_id)
            if state is None or state.get("nurse_decision") is None:
                return
            if done is not WritebackStatus.ok:
                outcome = await writeback.write_outcome(state)
                status = outcome.status
                logger.info(
                    "intake.writeback_consumer",
                    extra={"session_id": event.session_id, "status": status.value},
                )
                if status is WritebackStatus.failed:
                    reason = outcome.error or "write-back failed"
                    if outcome.retryable:
                        raise WritebackUnavailable(reason)
                    raise WritebackRejected(reason)
                if status is not WritebackStatus.ok:
                    return  # skipped (no EHR patient): nothing for the document to go with
            if briefings is None:
                return
            document = await briefings.ensure_document(state)
        if document.status is WritebackStatus.failed:
            reason = f"briefing document: {document.error or 'not written'}"
            if document.retryable:
                raise WritebackUnavailable(reason)
            raise WritebackRejected(reason)

    return handle


__all__ = [
    "WRITEBACK_GROUP",
    "DocumentWriter",
    "WritebackRejected",
    "WritebackUnavailable",
    "writeback_consumer",
]
