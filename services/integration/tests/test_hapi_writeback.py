"""The write-back against the seeded HAPI server (M1-09).

    make up && uv run pytest services/integration -m hapi

Each record is written under a fresh session id, so every run creates its own resources. They are
deleted at the end in reverse Bundle order, so no reference is left dangling. Skipped when HAPI does
not answer (conftest).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from careflow_contracts.generated.events.triage_confirmed_schema import TriageConfirmedData
from careflow_integration.adapters.loader import load_adapter
from careflow_integration.fhir.client import FhirClient
from careflow_integration.fhir.writeback.handler import WritebackHandler
from careflow_integration.fhir.writeback.ports import (
    MemoryWritebackStore,
    WritebackStatus,
    WrittenResource,
)
from careflow_integration.fhir.writeback.record import TriageRecord
from careflow_integration.fhir.writeback.validate import blocking_issues

pytestmark = pytest.mark.hapi

type Load = Callable[[str], TriageRecord]


class Records:
    def __init__(self, record: TriageRecord) -> None:
        self.record = record

    async def load(self, clinic_id: str, session_id: str) -> TriageRecord | None:
        return self.record


class Events:
    def __init__(self) -> None:
        self.published: list[tuple[str, BaseModel]] = []

    async def publish(self, event_type: str, data: BaseModel) -> None:
        self.published.append((event_type, data))


def fresh(record: TriageRecord) -> TriageRecord:
    sid = f"ses_hapi_{uuid.uuid4().hex[:12]}"
    decision = record.decision.model_copy(update={"session_id": sid})
    return record.model_copy(update={"session_id": sid, "decision": decision})


def confirmed(record: TriageRecord) -> TriageConfirmedData:
    d = record.decision
    return TriageConfirmedData(
        clinic_id=record.clinic_id,
        session_id=record.session_id,
        decision_id=d.decision_id,
        suggestion_id=d.suggestion_id,
        action=d.action,
        final_level=d.final_level,
        scale=d.scale,
        nurse_user_id=d.nurse_user_id,
        decided_at=d.decided_at,
    )


async def no_sleep(_: float) -> None:
    return None


async def delete(written: list[WrittenResource]) -> None:
    """Test clean-up only: the product's FhirClient has no delete."""
    async with httpx.AsyncClient(base_url=load_adapter().fhir_base_url + "/") as raw:
        for resource in reversed(written):
            await raw.delete(f"{resource.resource_type}/{resource.resource_id}")


async def found(client: FhirClient, resource_type: str, **params: str) -> int:
    return len(await client.search(resource_type, params))


async def test_the_chest_pain_triage_is_written_exactly_once(triage_record: Load) -> None:
    record = fresh(triage_record("chest_pain"))
    sid = record.session_id
    events = Events()
    async with FhirClient(load_adapter().fhir_base_url) as client:
        handler = WritebackHandler(
            records=Records(record),
            store=MemoryWritebackStore(),
            events=events,
            client=client,
            sleep=no_sleep,
        )
        first = await handler.on_triage_confirmed(confirmed(record))
        written = list(first.resources)
        try:
            assert (first.status, first.attempts) == (WritebackStatus.ok, 1), first.error
            assert len(written) == 16 and all(w.created for w in written)
            [encounter] = [w for w in written if w.resource_type == "Encounter"]
            ref = f"Encounter/{encounter.resource_id}"
            counts = {
                kind: await found(client, kind, encounter=ref)
                for kind in (
                    "Observation",
                    "QuestionnaireResponse",
                    "Flag",
                    "Communication",
                    "Task",
                )
            }
            assert counts == {
                "Observation": 10,  # HR, RR, SpO2, temp, BP, ACVPU, air, pain, NEWS2, triage level
                "QuestionnaireResponse": 1,
                "Flag": 1,
                "Communication": 1,
                "Task": 1,
            }
            assert await found(client, "Encounter", identifier=f"urn:careflow:session|{sid}") == 1

            again = await handler.on_triage_confirmed(confirmed(record))
            assert again.status is WritebackStatus.ok and not any(
                w.created for w in again.resources
            )
            assert [w.resource_id for w in again.resources] == [w.resource_id for w in written]
            for w in written:
                if w.resource_type == "Encounter":
                    query = {"identifier": f"urn:careflow:session|{sid}"}
                elif w.resource_type == "Provenance":
                    query = {"_tag": f"urn:careflow:writeback|{sid}"}
                else:
                    query = {"identifier": f"urn:careflow:fact|{sid}:{w.local_id}"}
                assert await found(client, w.resource_type, **query) == 1, w.local_id
            assert [e for e, _ in events.published] == ["fhir.write.completed"] * 2

            [task] = [w for w in written if w.resource_type == "Task"]
            stored = await client.read("Task", task.resource_id)
            [alert] = record.alerts
            end = datetime.fromisoformat(stored["restriction"]["period"]["end"])
            assert alert.deadline_at is not None and end == alert.deadline_at
            done = await handler.complete_task(record.clinic_id, sid, task.resource_id)
            completed = await client.read("Task", task.resource_id)
            assert (done.version_id, completed["status"]) == ("2", "completed")
            assert [t["code"] for t in completed["meta"]["tag"]] == ["PATIENT_STATED"]
        finally:
            await delete(written)


@pytest.mark.parametrize("name", ["chest_pain", "sepsis", "routine"])
async def test_every_written_resource_passes_validate(triage_record: Load, name: str) -> None:
    record = fresh(triage_record(name))
    async with FhirClient(load_adapter().fhir_base_url) as client:
        handler = WritebackHandler(
            records=Records(record),
            store=MemoryWritebackStore(),
            events=Events(),
            client=client,
            sleep=no_sleep,
        )
        outcome = await handler.on_triage_confirmed(confirmed(record))
        written = list(outcome.resources)
        try:
            assert outcome.status is WritebackStatus.ok, outcome.error
            problems: dict[str, list[Any]] = {}
            for w in written:
                stored = await client.read(w.resource_type, w.resource_id)
                if issues := await blocking_issues(client, stored):
                    problems[w.local_id] = issues
            assert problems == {}
        finally:
            await delete(written)
