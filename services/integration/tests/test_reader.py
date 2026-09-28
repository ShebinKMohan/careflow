"""FhirEhrReader on the recorded persona answers: status, facts, degraded mode and the breaker."""

from __future__ import annotations

import asyncio
import copy
import socket
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx
import pytest
import respx

from careflow_contracts.generated.ehr_fetch_schema import EhrStatus, PatientIdentifier
from careflow_integration.adapters.loader import HospitalAdapter
from careflow_integration.adapters.mapper import UNCODED_SYSTEM, coding
from careflow_integration.fhir.client import FhirClient
from careflow_integration.fhir.reader import CircuitBreaker, FhirEhrReader

KEYPAD = "https://careflow.example/fhir/sid/keypad-id"


def reader(adapter: HospitalAdapter, now: datetime, **kwargs: Any) -> FhirEhrReader:
    async def no_sleep(_: float) -> None:
        return None

    client = FhirClient(adapter.fhir_base_url, sleep=no_sleep)
    return FhirEhrReader(adapter, client=client, clock=lambda: now, **kwargs)


def keypad(value: str = "100001") -> PatientIdentifier:
    return PatientIdentifier(system=KEYPAD, value=value)


@respx.mock
async def test_the_chest_pain_persona_reads_as_ehr_facts(
    adapter: HospitalAdapter, now: datetime, recorded: Callable[..., Any], serve: Callable[..., Any]
) -> None:
    serve(respx.mock, recorded("chest-pain"))
    fetch = await reader(adapter, now).fetch_context(keypad(), "clinic_a")
    assert fetch.status is EhrStatus.ok and fetch.errors == []
    assert fetch.patient_ref == "Patient/persona-chest-pain"
    assert fetch.patient is not None and fetch.patient.fhir_patient_id == "persona-chest-pain"
    assert fetch.patient.age_years is not None and fetch.patient.age_years.value == 58
    assert fetch.patient.age_months is None  # exact months only under 5 years
    assert fetch.patient.sex is not None and fetch.patient.sex.value.code == "male"
    assert fetch.context is not None
    [condition] = fetch.context.conditions or []
    assert (
        condition.value.display == "Hypertensive disorder" and condition.provenance.value == "EHR"
    )
    assert condition.source.model_dump(exclude_none=True) == {
        "kind": "fhir",
        "resource_type": "Condition",
        "id": "persona-chest-pain-condition-1",
        "version_id": "1",
    }
    assert [m.value.display for m in fetch.context.medications or []] == ["amlodipine"]
    assert fetch.context.allergies == []


@respx.mock
async def test_the_request_asks_hapi_for_the_right_searches(
    adapter: HospitalAdapter, now: datetime, recorded: Callable[..., Any], serve: Callable[..., Any]
) -> None:
    router = serve(respx.mock, recorded("routine"))
    fetch = await reader(adapter, now).fetch_context(keypad("100005"), "clinic_a")
    asked = {
        call.request.url.path.removesuffix("/_search").rsplit("/", 1)[-1]: dict(
            httpx.QueryParams(call.request.content.decode())
        )
        for call in router.calls
    }
    assert all(call.request.url.query == b"" for call in router.calls)  # nothing in the URL
    assert asked["Patient"]["identifier"] == f"{KEYPAD}|100005"
    assert asked["AllergyIntolerance"]["clinical-status"] == "active"
    assert asked["Condition"]["clinical-status"] == "active"
    assert (
        asked["MedicationStatement"]["status"] == asked["MedicationRequest"]["status"] == "active"
    )
    assert asked["Appointment"]["date"] == "ge2026-09-26"
    assert [(a.status, a.start.isoformat() if a.start else None) for a in fetch.appointments] == [
        ("booked", "2026-09-26T10:00:00+00:00")
    ]


@respx.mock
async def test_a_child_gets_an_exact_age_in_months(
    adapter: HospitalAdapter, now: datetime, recorded: Callable[..., Any], serve: Callable[..., Any]
) -> None:
    serve(respx.mock, recorded("child-drowsy"))
    fetch = await reader(adapter, now).fetch_context(keypad("100003"), "clinic_a")
    patient = fetch.patient
    assert patient is not None and patient.age_years is not None and patient.age_months is not None
    assert (patient.age_years.value, patient.age_months.value) == (2, 26)


@respx.mock
async def test_some_searches_failing_is_partial(
    adapter: HospitalAdapter, now: datetime, recorded: Callable[..., Any], serve: Callable[..., Any]
) -> None:
    serve(respx.mock, recorded("chest-pain"), fail={"Condition": 500})
    fetch = await reader(adapter, now).fetch_context(keypad(), "clinic_a")
    assert fetch.status is EhrStatus.partial and fetch.errors == ["Condition: HTTP 500"]
    assert fetch.context is not None and fetch.context.conditions == []
    assert [m.value.display for m in fetch.context.medications or []] == ["amlodipine"]


@respx.mock
async def test_all_searches_failing_is_unavailable(
    adapter: HospitalAdapter, now: datetime, recorded: Callable[..., Any], serve: Callable[..., Any]
) -> None:
    kinds = [
        "AllergyIntolerance",
        "Condition",
        "MedicationStatement",
        "MedicationRequest",
        "Appointment",
    ]
    serve(respx.mock, recorded("chest-pain"), fail=dict.fromkeys(kinds, 503))
    fetch = await reader(adapter, now).fetch_context(keypad(), "clinic_a")
    assert fetch.status is EhrStatus.unavailable and len(fetch.errors) == 5
    assert fetch.context is None and fetch.patient is None


@respx.mock
async def test_the_patient_search_failing_is_unavailable(
    adapter: HospitalAdapter, now: datetime, serve: Callable[..., Any]
) -> None:
    serve(respx.mock, {}, fail={"Patient": 503})
    fetch = await reader(adapter, now).fetch_context(keypad(), "clinic_a")
    assert fetch.status is EhrStatus.unavailable and fetch.errors == ["Patient search: HTTP 503"]


@pytest.mark.parametrize(
    ("patients", "status"), [(0, EhrStatus.not_found), (2, EhrStatus.ambiguous)]
)
@respx.mock
async def test_no_patient_or_several_are_reported(
    adapter: HospitalAdapter,
    now: datetime,
    recorded: Callable[..., Any],
    serve: Callable[..., Any],
    patients: int,
    status: EhrStatus,
) -> None:
    [patient] = recorded("chest-pain")["Patient"]
    twin = {**copy.deepcopy(patient), "id": "persona-chest-pain-twin"}
    serve(respx.mock, {"Patient": [patient, twin][:patients]})
    fetch = await reader(adapter, now).fetch_context(keypad(), "clinic_a")
    assert fetch.status is status and fetch.patient_ref is None and fetch.context is None
    assert fetch.errors == [f"{patients} patients match"]


@respx.mock
async def test_an_unparseable_resource_is_skipped(
    adapter: HospitalAdapter, now: datetime, recorded: Callable[..., Any], serve: Callable[..., Any]
) -> None:
    answers = recorded("chest-pain")
    answers["Condition"] = [
        *answers["Condition"],
        {"resourceType": "Condition", "id": "bad", "subject": "x", "code": {"text": "Asthma"}},
    ]
    serve(respx.mock, answers)
    fetch = await reader(adapter, now).fetch_context(keypad(), "clinic_a")
    assert fetch.status is EhrStatus.ok and fetch.context is not None
    ids = [c.source.model_dump()["id"] for c in fetch.context.conditions or []]
    assert ids == ["persona-chest-pain-condition-1"]


async def test_an_ehr_that_never_answers_is_unavailable_within_the_budget(
    adapter: HospitalAdapter, now: datetime
) -> None:
    """A server that accepts connections but never answers: the overall budget ends the wait."""

    async def silent(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await asyncio.sleep(60)

    server = await asyncio.start_server(silent, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    quick = adapter.model_copy(
        update={
            "fhir_base_url": f"http://127.0.0.1:{port}/fhir",
            "timeouts": adapter.timeouts.model_copy(update={"total_s": 0.5}),
        }
    )
    try:
        start = time.perf_counter()
        fetch = await FhirEhrReader(quick, clock=lambda: now).fetch_context(keypad(), "clinic_a")
        elapsed = time.perf_counter() - start
    finally:
        server.close()
    assert fetch.status is EhrStatus.unavailable and fetch.errors == ["no answer within 0.5 s"]
    assert elapsed < 1.5


async def test_an_ehr_that_is_down_is_unavailable_within_8_s(
    adapter: HospitalAdapter, now: datetime
) -> None:
    """Nothing listening (HAPI stopped): refused connections are retried twice, then unavailable."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    down = adapter.model_copy(update={"fhir_base_url": f"http://127.0.0.1:{port}/fhir"})
    start = time.perf_counter()
    fetch = await FhirEhrReader(down, clock=lambda: now).fetch_context(keypad(), "clinic_a")
    elapsed = time.perf_counter() - start
    assert fetch.status is EhrStatus.unavailable and fetch.errors == [
        "Patient search: ConnectError"
    ]
    assert elapsed <= 8.0


@respx.mock
async def test_the_breaker_opens_after_3_failures_and_closes_after_30_s(
    adapter: HospitalAdapter, now: datetime, recorded: Callable[..., Any], serve: Callable[..., Any]
) -> None:
    ticks = [0.0]
    breaker = CircuitBreaker(clock=lambda: ticks[0])
    ehr = reader(adapter, now, breaker=breaker)
    router = serve(respx.mock, {}, fail={"Patient": 503})
    for _ in range(3):
        assert (await ehr.fetch_context(keypad(), "clinic_a")).status is EhrStatus.unavailable
    calls = router.calls.call_count
    refused = await ehr.fetch_context(keypad(), "clinic_a")
    assert refused.status is EhrStatus.unavailable and refused.errors == [
        "circuit open after repeated EHR failures"
    ]
    assert router.calls.call_count == calls  # no request while open
    ticks[0] = 29.0
    assert (await ehr.fetch_context(keypad(), "clinic_a")).errors[0].startswith("circuit open")
    ticks[0] = 30.0
    respx.mock.routes.clear()
    serve(respx.mock, recorded("chest-pain"))
    closed = await ehr.fetch_context(keypad(), "clinic_a")
    assert closed.status is EhrStatus.ok  # a success after the cooldown closes it
    assert breaker.failures == 0 and not breaker.is_open


@respx.mock
async def test_a_failure_after_the_cooldown_opens_the_breaker_again(
    adapter: HospitalAdapter, now: datetime, serve: Callable[..., Any]
) -> None:
    ticks = [0.0]
    ehr = reader(adapter, now, breaker=CircuitBreaker(clock=lambda: ticks[0]))
    router = serve(respx.mock, {}, fail={"Patient": 503})
    for _ in range(3):
        await ehr.fetch_context(keypad(), "clinic_a")
    ticks[0] = 30.0
    tried = await ehr.fetch_context(keypad(), "clinic_a")
    assert tried.errors == ["Patient search: HTTP 503"]  # let through, and failed
    calls = router.calls.call_count
    again = await ehr.fetch_context(keypad(), "clinic_a")
    assert again.errors == ["circuit open after repeated EHR failures"]
    assert router.calls.call_count == calls


def test_display_preference_and_text_without_a_coding(adapter: HospitalAdapter) -> None:
    concept = {
        "text": "Peanut allergy",
        "coding": [
            {
                "system": "http://snomed.info/sct",
                "code": "91935009",
                "display": "Allergy to peanuts",
            }
        ],
    }
    assert coding(concept, adapter) == {
        "system": "http://snomed.info/sct",
        "code": "91935009",
        "display": "Peanut allergy",
    }
    assert coding({"text": "Something the EHR did not code"}, adapter) == {
        "system": UNCODED_SYSTEM,
        "code": "uncoded",
        "display": "Something the EHR did not code",
    }
    assert coding({"coding": [{"system": "http://snomed.info/sct"}]}, adapter) is None
