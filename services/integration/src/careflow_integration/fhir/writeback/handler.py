"""`WritebackHandler.on_triage_confirmed`: write a confirmed triage to the hospital's FHIR server
(M1-09), and `complete_task`: mark a written Task done.

The handler loads the session's `TriageRecord`, checks it holds the decision the event confirms
(safety rule 3), builds the transaction Bundle and posts it. Connection errors, timeouts and HTTP
429/502/503/504 are retried 3 times with backoff; then the status is `failed`. An EHR problem never
raises: the outcome is recorded and returned. Once M1-11's bus exists this runs as consumer group
`fhir-writeback`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Final

from pydantic import ValidationError

from careflow_contracts.events import FhirWriteCompletedData
from careflow_contracts.generated.events.triage_confirmed_schema import TriageConfirmedData
from careflow_integration.adapters.loader import HospitalAdapter, load_adapter
from careflow_integration.fhir.client import RETRY_STATUSES, FhirClient, FhirError
from careflow_integration.fhir.writeback.builders import instant
from careflow_integration.fhir.writeback.bundle import (
    build_transaction,
    bundle_id,
    written_resources,
)
from careflow_integration.fhir.writeback.ports import (
    EventPublisher,
    TriageRecordSource,
    WritebackOutcome,
    WritebackStatus,
    WritebackStore,
    WrittenResource,
)
from careflow_integration.fhir.writeback.record import TriageRecord

logger = logging.getLogger("careflow_integration.fhir")

type Json = dict[str, Any]

#: Waits before the 3 retries that follow the first attempt.
RETRY_DELAYS: Final = (1.0, 2.0, 4.0)
WRITE_COMPLETED: Final = "fhir.write.completed"
CONSUMER_GROUP: Final = "fhir-writeback"


class UnknownTask(LookupError):
    """The Task is not one this session wrote."""


def mismatch(record: TriageRecord, event: TriageConfirmedData) -> str | None:
    """Why the record is not the triage the event confirms, or None."""
    decision = record.decision
    if (record.clinic_id, record.session_id) != (event.clinic_id, event.session_id):
        return "the record belongs to another session"
    if decision.decision_id != event.decision_id:
        return "the record holds another nurse decision"
    if (int(decision.final_level), decision.scale) != (int(event.final_level), event.scale):
        return "the record's level differs from the confirmed level"
    return None


def describe(exc: Exception) -> str:
    """A short reason without patient data (validation errors are counted, not quoted)."""
    if isinstance(exc, ValidationError):
        return f"{exc.error_count()} validation errors in {exc.title}"
    return str(exc)[:200]


class WritebackHandler:
    def __init__(
        self,
        *,
        records: TriageRecordSource,
        store: WritebackStore,
        events: EventPublisher,
        adapter: HospitalAdapter | None = None,
        client: FhirClient | None = None,
        retry_delays: Sequence[float] = RETRY_DELAYS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.records, self.store, self.events = records, store, events
        self.adapter = adapter or load_adapter()
        self.client = client or FhirClient(self.adapter.fhir_base_url)
        self.retry_delays = tuple(retry_delays)
        self.sleep = sleep
        self.clock = clock

    async def _finish(self, outcome: WritebackOutcome) -> WritebackOutcome:
        await self.store.save(outcome)
        log = logger.info if outcome.status is WritebackStatus.ok else logger.warning
        log(
            "fhir.writeback",
            extra={
                "clinic_id": outcome.clinic_id,
                "session_id": outcome.session_id,
                "status": outcome.status.value,
                "attempts": outcome.attempts,
                "error": outcome.error,
                "resources_created": sum(r.created for r in outcome.resources),
            },
        )
        return outcome

    async def on_triage_confirmed(self, event: TriageConfirmedData) -> WritebackOutcome:
        def outcome(status: WritebackStatus, error: str, attempts: int = 0) -> WritebackOutcome:
            return WritebackOutcome(
                event.clinic_id, event.session_id, status, attempts, error=error
            )

        record = await self.records.load(event.clinic_id, event.session_id)
        if record is None:
            return await self._finish(outcome(WritebackStatus.failed, "no triage record"))
        if (problem := mismatch(record, event)) is not None:
            return await self._finish(outcome(WritebackStatus.failed, problem))
        if record.patient_ref is None:
            return await self._finish(
                outcome(WritebackStatus.skipped, "no EHR patient to write to")
            )
        try:
            bundle = build_transaction(record, self.adapter)
        except (ValidationError, ValueError, LookupError) as exc:
            reason = f"bundle not built: {describe(exc)}"
            return await self._finish(outcome(WritebackStatus.failed, reason))
        attempts, error, retryable = 0, "", False
        for delay in [*self.retry_delays, None]:
            attempts += 1
            try:
                resources = written_resources(bundle, await self.client.transaction(bundle))
            except FhirError as exc:
                error = str(exc)
                retryable = exc.status is None or exc.status in RETRY_STATUSES
                if delay is None or not retryable:
                    break
                await self.sleep(delay)
                continue
            written = WritebackOutcome(
                event.clinic_id,
                event.session_id,
                WritebackStatus.ok,
                attempts,
                bundle_id=bundle_id(record),
                resources=tuple(resources),
            )
            await self._finish(written)
            data = FhirWriteCompletedData(
                clinic_id=event.clinic_id,
                session_id=event.session_id,
                bundle_id=bundle_id(record),
                resource_count=len(resources),
            )
            await self.events.publish(WRITE_COMPLETED, data)
            return written
        failed = WritebackOutcome(
            event.clinic_id,
            event.session_id,
            WritebackStatus.failed,
            attempts,
            error=error,
            retryable=retryable,
        )
        return await self._finish(failed)

    async def complete_task(self, clinic_id: str, session_id: str, task_id: str) -> WrittenResource:
        """Set a Task this session wrote to `completed`, as a versioned update (`If-Match`). An
        already completed Task is left as it is. FHIR errors (e.g. 412, changed meanwhile) raise."""
        known = next(
            (
                r
                for r in await self.store.resources(clinic_id, session_id)
                if r.resource_type == "Task" and r.resource_id == task_id
            ),
            None,
        )
        if known is None:
            raise UnknownTask(task_id)
        task = await self.client.read("Task", task_id)
        if task.get("status") != "completed":
            body: Json = {k: v for k, v in task.items() if k != "meta"}
            body |= {"meta": {"tag": task.get("meta", {}).get("tag", [])}}
            body |= {"status": "completed", "lastModified": instant(self.clock())}
            task = await self.client.update(body, version_id=str(task["meta"]["versionId"]))
        updated = WrittenResource(
            local_id=known.local_id,
            resource_type="Task",
            resource_id=task_id,
            version_id=str(task["meta"]["versionId"]),
        )
        await self.store.update_resource(clinic_id, session_id, updated)
        return updated


__all__ = [
    "CONSUMER_GROUP",
    "RETRY_DELAYS",
    "WRITE_COMPLETED",
    "UnknownTask",
    "WritebackHandler",
    "mismatch",
]
