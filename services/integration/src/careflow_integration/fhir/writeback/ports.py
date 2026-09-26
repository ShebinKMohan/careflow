"""What the write-back needs from the rest of CareFlow, and what it reports (M1-09).

The API implements `TriageRecordSource` and `WritebackStore` over Postgres (`fhir_writebacks`,
`fhir_writeback_status`) and `EventPublisher` over the event bus (M1-11/M1-12).
`MemoryWritebackStore` serves tests and single-process demos.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel

from careflow_integration.fhir.writeback.record import TriageRecord


class WritebackStatus(StrEnum):
    ok = "ok"
    failed = "failed"
    #: Nothing to write to: the patient has no EHR record (not identified at check-in).
    skipped = "skipped"


@dataclass(frozen=True)
class WrittenResource:
    """One resource the server holds for the session, by its local id (e.g. `task-...`)."""

    local_id: str
    resource_type: str
    resource_id: str
    version_id: str | None
    #: Created by this write (201), rather than found already there (200).
    created: bool = False


@dataclass(frozen=True)
class WritebackOutcome:
    clinic_id: str
    session_id: str
    status: WritebackStatus
    attempts: int
    bundle_id: str | None = None
    #: Short reason for a failed or skipped write; never patient data.
    error: str | None = None
    resources: tuple[WrittenResource, ...] = ()


class TriageRecordSource(Protocol):
    async def load(self, clinic_id: str, session_id: str) -> TriageRecord | None:
        """The session's confirmed triage, or None when there is none."""
        ...


class WritebackStore(Protocol):
    async def save(self, outcome: WritebackOutcome) -> None:
        """Record the status (and, when ok, the written resources) for the session."""
        ...

    async def resources(self, clinic_id: str, session_id: str) -> list[WrittenResource]: ...

    async def update_resource(
        self, clinic_id: str, session_id: str, resource: WrittenResource
    ) -> None:
        """Record a new version of an already written resource (e.g. a completed Task)."""
        ...


class EventPublisher(Protocol):
    async def publish(self, event_type: str, data: BaseModel) -> None: ...


class MemoryWritebackStore:
    """`WritebackStore` in memory."""

    def __init__(self) -> None:
        self.outcomes: dict[tuple[str, str], WritebackOutcome] = {}
        self.written: dict[tuple[str, str], dict[str, WrittenResource]] = defaultdict(dict)

    async def save(self, outcome: WritebackOutcome) -> None:
        key = (outcome.clinic_id, outcome.session_id)
        self.outcomes[key] = outcome
        for resource in outcome.resources:
            self.written[key][resource.local_id] = replace(resource, created=False)

    async def resources(self, clinic_id: str, session_id: str) -> list[WrittenResource]:
        return list(self.written[(clinic_id, session_id)].values())

    async def update_resource(
        self, clinic_id: str, session_id: str, resource: WrittenResource
    ) -> None:
        self.written[(clinic_id, session_id)][resource.local_id] = resource


__all__ = [
    "EventPublisher",
    "MemoryWritebackStore",
    "TriageRecordSource",
    "WritebackOutcome",
    "WritebackStatus",
    "WritebackStore",
    "WrittenResource",
]
