"""Bus consumers for the intake (M1-12).

`fhir-writeback` (M1-09): on `triage.confirmed`, write the session's triage back to the EHR. The
graph already writes it when the nurse decides, so this is the at-least-once safety net (after a
restart, or while the EHR was down); every write is a conditional create, so a second one stores
nothing new. A failed write is never acknowledged (ADR-043): when the EHR was unreachable the event
stays pending and is retried until the write succeeds; when the EHR rejected it (or no Bundle
could be built) it goes to the dead-letter stream with the reason.
"""

from __future__ import annotations

import logging
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from careflow_api.events.bus import BusEvent, Handler, PermanentFailure, TransientFailure
from careflow_api.intake.ports import WritebackPort
from careflow_api.intake.runtime import IntakeRuntime
from careflow_integration.fhir.writeback.ports import WritebackStatus

logger = logging.getLogger("careflow_api.intake")

WRITEBACK_GROUP: Final = "fhir-writeback"


class WritebackUnavailable(TransientFailure):
    """The EHR did not answer the write (or answered 429/502/503/504): retry later."""


class WritebackRejected(PermanentFailure):
    """The EHR rejected the write, or no Bundle could be built: dead-letter it with the reason."""


def writeback_consumer(runtime: IntakeRuntime, writeback: WritebackPort) -> Handler:
    async def handle(db: AsyncSession, event: BusEvent) -> None:
        if event.type != "triage.confirmed" or event.session_id is None:
            return
        # The session lock waits for the graph's own step (decision, write-back, briefing), so the
        # two never post the same Bundle at once (HAPI answers a concurrent twin with 409).
        async with runtime.serialized(event.session_id):
            done = await writeback.store.status(str(event.clinic_id), event.session_id)
            if done is WritebackStatus.ok:
                return
            state = await runtime.state(event.session_id)
            if state is None or state.get("nurse_decision") is None:
                return
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

    return handle


__all__ = ["WRITEBACK_GROUP", "WritebackRejected", "WritebackUnavailable", "writeback_consumer"]
