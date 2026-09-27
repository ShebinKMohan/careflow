"""WritebackHandler (M1-09): retries, status record, the internal event and complete_task."""

from __future__ import annotations

import json
import socket
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx
from pydantic import BaseModel

from careflow_contracts.events import FhirWriteCompletedData
from careflow_contracts.generated.events.triage_confirmed_schema import TriageConfirmedData
from careflow_integration.adapters.loader import HospitalAdapter
from careflow_integration.fhir.client import FhirClient
from careflow_integration.fhir.writeback.bundle import bundle_id
from careflow_integration.fhir.writeback.handler import UnknownTask, WritebackHandler
from careflow_integration.fhir.writeback.ports import (
    MemoryWritebackStore,
    WritebackStatus,
    WrittenResource,
)
from careflow_integration.fhir.writeback.record import TriageRecord

type Json = dict[str, Any]
type Load = Callable[[str], TriageRecord]


class Records:
    def __init__(self, *records: TriageRecord) -> None:
        self.records = {(r.clinic_id, r.session_id): r for r in records}

    async def load(self, clinic_id: str, session_id: str) -> TriageRecord | None:
        return self.records.get((clinic_id, session_id))


class Events:
    def __init__(self) -> None:
        self.published: list[tuple[str, BaseModel]] = []

    async def publish(self, event_type: str, data: BaseModel) -> None:
        self.published.append((event_type, data))


def confirmed(record: TriageRecord, **changes: Any) -> TriageConfirmedData:
    d = record.decision
    body = {
        "clinic_id": record.clinic_id,
        "session_id": record.session_id,
        "decision_id": d.decision_id,
        "suggestion_id": d.suggestion_id,
        "action": d.action,
        "final_level": d.final_level,
        "scale": d.scale,
        "nurse_user_id": d.nurse_user_id,
        "decided_at": d.decided_at,
    }
    return TriageConfirmedData.model_validate(body | changes)


def created(request: httpx.Request) -> httpx.Response:
    """A HAPI-like transaction-response: every entry created, ids 100, 101, ..."""
    bundle = json.loads(request.content)
    assert request.headers["Content-Type"] == "application/fhir+json"
    entries = [
        {
            "response": {
                "status": "201 Created",
                "location": f"{e['request']['url']}/{100 + i}/_history/1",
            }
        }
        for i, e in enumerate(bundle["entry"])
    ]
    return httpx.Response(
        200, json={"resourceType": "Bundle", "type": "transaction-response", "entry": entries}
    )


class Setup:
    def __init__(self, record: TriageRecord, adapter: HospitalAdapter, **kwargs: Any) -> None:
        self.sleeps: list[float] = []
        self.store = MemoryWritebackStore()
        self.events = Events()

        async def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)

        self.handler = WritebackHandler(
            records=Records(record),
            store=self.store,
            events=self.events,
            adapter=adapter,
            client=FhirClient(adapter.fhir_base_url, sleep=sleep),
            sleep=sleep,
            **kwargs,
        )


@respx.mock
async def test_a_confirmed_triage_is_written_recorded_and_announced(
    triage_record: Load, adapter: HospitalAdapter
) -> None:
    record = triage_record("chest_pain")
    route = respx.post(f"{adapter.fhir_base_url}/").mock(side_effect=created)
    s = Setup(record, adapter)
    outcome = await s.handler.on_triage_confirmed(confirmed(record))
    assert (outcome.status, outcome.attempts, outcome.error) == (WritebackStatus.ok, 1, None)
    assert route.call_count == 1 and len(outcome.resources) == 16
    assert s.store.outcomes[(record.clinic_id, record.session_id)] == outcome
    task = next(
        r
        for r in await s.store.resources(record.clinic_id, record.session_id)
        if r.resource_type == "Task"
    )
    assert (task.local_id, task.resource_id, task.version_id) == (
        "task-ecg_within_10_min",
        "113",
        "1",
    )
    [(event_type, data)] = s.events.published
    assert event_type == "fhir.write.completed"
    assert data == FhirWriteCompletedData(
        clinic_id="clinic_demo",
        session_id="ses_chest_pain",
        bundle_id=bundle_id(record),
        resource_count=16,
    )


async def test_hapi_down_fails_after_3_retries_and_nothing_raises(
    triage_record: Load, adapter: HospitalAdapter
) -> None:
    """Nothing listening on the FHIR port: 1 attempt + 3 retries with backoff, then `failed`."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    down = adapter.model_copy(update={"fhir_base_url": f"http://127.0.0.1:{port}/fhir"})
    record = triage_record("chest_pain")
    s = Setup(record, down)
    outcome = await s.handler.on_triage_confirmed(confirmed(record))
    assert (outcome.status, outcome.attempts, outcome.error) == (
        WritebackStatus.failed,
        4,
        "ConnectError",
    )
    assert outcome.retryable is True  # the bus consumer keeps the event pending (ADR-043)
    assert s.sleeps == [1.0, 2.0, 4.0]
    assert s.store.outcomes[(record.clinic_id, record.session_id)].status is WritebackStatus.failed
    assert (
        s.events.published == []
        and await s.store.resources(record.clinic_id, record.session_id) == []
    )


@respx.mock
async def test_a_503_is_retried_and_a_400_is_not(
    triage_record: Load, adapter: HospitalAdapter
) -> None:
    record = triage_record("chest_pain")
    seen: list[httpx.Request] = []

    def unavailable_once(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(503) if len(seen) == 1 else created(request)

    respx.post(f"{adapter.fhir_base_url}/").mock(side_effect=unavailable_once)
    s = Setup(record, adapter)
    outcome = await s.handler.on_triage_confirmed(confirmed(record))
    assert (outcome.status, outcome.attempts, s.sleeps) == (WritebackStatus.ok, 2, [1.0])
    respx.post(f"{adapter.fhir_base_url}/").mock(return_value=httpx.Response(400))
    s = Setup(record, adapter)
    outcome = await s.handler.on_triage_confirmed(confirmed(record))
    assert (outcome.status, outcome.attempts, outcome.error) == (
        WritebackStatus.failed,
        1,
        "HTTP 400",
    )
    assert outcome.retryable is False  # a rejection: retrying cannot help
    assert s.sleeps == []


@respx.mock
async def test_a_server_that_stays_unavailable_is_a_retryable_failure(
    triage_record: Load, adapter: HospitalAdapter
) -> None:
    record = triage_record("chest_pain")
    respx.post(f"{adapter.fhir_base_url}/").mock(return_value=httpx.Response(503))
    s = Setup(record, adapter)
    outcome = await s.handler.on_triage_confirmed(confirmed(record))
    assert (outcome.status, outcome.attempts, outcome.error, outcome.retryable) == (
        WritebackStatus.failed,
        4,
        "HTTP 503",
        True,
    )
    assert s.sleeps == [1.0, 2.0, 4.0]


@respx.mock
@pytest.mark.parametrize(
    ("change", "status", "error"),
    [
        (
            {"decision_id": "dec_other"},
            WritebackStatus.failed,
            "the record holds another nurse decision",
        ),
        (
            {"final_level": 1},
            WritebackStatus.failed,
            "the record's level differs from the confirmed level",
        ),
        ({"session_id": "ses_unknown"}, WritebackStatus.failed, "no triage record"),
    ],
)
async def test_only_the_confirmed_decision_is_ever_written(
    triage_record: Load, adapter: HospitalAdapter, change: Json, status: WritebackStatus, error: str
) -> None:
    """Safety rule 3: a record that is not the triage the event confirms is never written."""
    record = triage_record("chest_pain")
    route = respx.post(f"{adapter.fhir_base_url}/").mock(side_effect=created)
    s = Setup(record, adapter)
    outcome = await s.handler.on_triage_confirmed(confirmed(record, **change))
    assert (outcome.status, outcome.error, outcome.retryable) == (status, error, False)
    assert route.call_count == 0 and s.events.published == []


@respx.mock
async def test_a_patient_without_an_ehr_record_is_skipped(
    triage_record: Load, adapter: HospitalAdapter
) -> None:
    record = triage_record("chest_pain").model_copy(update={"patient_ref": None})
    route = respx.post(f"{adapter.fhir_base_url}/").mock(side_effect=created)
    s = Setup(record, adapter)
    outcome = await s.handler.on_triage_confirmed(confirmed(record))
    assert (outcome.status, outcome.error) == (
        WritebackStatus.skipped,
        "no EHR patient to write to",
    )
    assert route.call_count == 0


def stored_task(version: str = "1", status: str = "requested") -> Json:
    return {
        "resourceType": "Task",
        "id": "113",
        "meta": {
            "versionId": version,
            "lastUpdated": "2026-09-26T09:06:01Z",
            "tag": [{"code": "PATIENT_STATED"}],
        },
        "status": status,
        "intent": "order",
    }


@respx.mock
async def test_complete_task_is_a_versioned_update(
    triage_record: Load, adapter: HospitalAdapter
) -> None:
    record = triage_record("chest_pain")
    s = Setup(record, adapter)
    await s.store.update_resource(
        record.clinic_id,
        record.session_id,
        WrittenResource("task-ecg_within_10_min", "Task", "113", "1"),
    )
    respx.get(f"{adapter.fhir_base_url}/Task/113").mock(
        return_value=httpx.Response(200, json=stored_task())
    )
    put = respx.put(f"{adapter.fhir_base_url}/Task/113").mock(
        return_value=httpx.Response(200, json=stored_task("2", "completed"))
    )
    done = await s.handler.complete_task(record.clinic_id, record.session_id, "113")
    assert (done.version_id, done.local_id) == ("2", "task-ecg_within_10_min")
    request = put.calls.last.request
    assert request.headers["If-Match"] == 'W/"1"'
    sent = json.loads(request.content)
    assert sent["status"] == "completed" and sent["meta"] == {"tag": [{"code": "PATIENT_STATED"}]}
    assert (await s.store.resources(record.clinic_id, record.session_id))[0].version_id == "2"
    with pytest.raises(UnknownTask):
        await s.handler.complete_task(record.clinic_id, record.session_id, "999")


@respx.mock
async def test_an_already_completed_task_is_left_alone(
    triage_record: Load, adapter: HospitalAdapter
) -> None:
    record = triage_record("chest_pain")
    s = Setup(record, adapter)
    await s.store.update_resource(
        record.clinic_id,
        record.session_id,
        WrittenResource("task-ecg_within_10_min", "Task", "113", "2"),
    )
    respx.get(f"{adapter.fhir_base_url}/Task/113").mock(
        return_value=httpx.Response(200, json=stored_task("2", "completed"))
    )
    put = respx.put(f"{adapter.fhir_base_url}/Task/113")
    assert (
        await s.handler.complete_task(record.clinic_id, record.session_id, "113")
    ).version_id == "2"
    assert put.call_count == 0
