"""FhirClient: paging, retries on the right errors only, no paging off the server, and search
parameters in the body, never in the URL."""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest
import respx

from careflow_integration.fhir.client import MAX_PAGES, FhirClient, FhirError

BASE = "http://fhir.test/fhir"


def page(ids: list[str], next_url: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "resourceType": "Bundle",
        "type": "searchset",
        "entry": [{"resource": {"resourceType": "Condition", "id": i}} for i in ids],
    }
    if next_url:
        body["link"] = [{"relation": "self", "url": BASE}, {"relation": "next", "url": next_url}]
    return body


def make_client(sleeps: list[float]) -> FhirClient:
    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return FhirClient(BASE, sleep=sleep)


def form(request: httpx.Request) -> dict[str, str]:
    return dict(httpx.QueryParams(request.content.decode()))


@respx.mock
async def test_search_follows_next_links_and_asks_for_100_per_page() -> None:
    first = respx.post(f"{BASE}/Condition/_search").mock(
        return_value=httpx.Response(
            200, json=page(["c1", "c2"], f"{BASE}?_getpages=abc&_getpagesoffset=2")
        )
    )
    respx.get(BASE, params={"_getpages": "abc"}).mock(
        return_value=httpx.Response(200, json=page(["c3"]))
    )
    async with make_client([]) as client:
        found = await client.search("Condition", {"patient": "p1"})
    assert [r["id"] for r in found] == ["c1", "c2", "c3"]
    request = first.calls.last.request
    assert form(request) == {"patient": "p1", "_count": "100"}
    assert request.headers["Accept"] == "application/fhir+json"


@respx.mock
async def test_a_search_never_puts_its_parameters_in_the_url(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An identifier in a URL is in every request log on the way (M2-05): httpx logs the URL."""
    caplog.set_level(logging.INFO)
    card = "784-1992-1215279-9"  # a persona's synthetic card number
    route = respx.post(f"{BASE}/Patient/_search").mock(
        return_value=httpx.Response(200, json=page(["p1"]))
    )
    async with make_client([]) as client:
        await client.search("Patient", {"identifier": f"urn:mock-id|{card}"})
    request = route.calls.last.request
    assert request.url.query == b"" and card not in str(request.url)
    assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert form(request)["identifier"] == f"urn:mock-id|{card}"
    logged = [record.getMessage() for record in caplog.records]
    assert any("Patient/_search" in line for line in logged)  # the request was logged...
    assert not any(card in line for line in logged)  # ...without the card number


@respx.mock
async def test_paging_stops_after_5_pages() -> None:
    looping = httpx.Response(200, json=page(["c"], f"{BASE}?_getpages=loop"))
    respx.post(f"{BASE}/Condition/_search").mock(return_value=looping)
    respx.get(url__startswith=BASE).mock(return_value=looping)
    async with make_client([]) as client:
        found = await client.search("Condition", {})
    assert len(found) == MAX_PAGES == 5


@respx.mock
async def test_a_next_link_to_another_server_is_refused() -> None:
    respx.post(f"{BASE}/Condition/_search").mock(
        return_value=httpx.Response(
            200, json=page(["c1"], "http://elsewhere.test/fhir?_getpages=x")
        )
    )
    async with make_client([]) as client:
        with pytest.raises(FhirError, match="outside the FHIR server"):
            await client.search("Condition", {})


@respx.mock
async def test_a_503_is_retried_then_succeeds() -> None:
    route = respx.post(f"{BASE}/Patient/_search").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=page(["p1"]))]
    )
    sleeps: list[float] = []
    async with make_client(sleeps) as client:
        found = await client.search("Patient", {"identifier": "sys|1"})
    assert [r["id"] for r in found] == ["p1"] and route.call_count == 2 and sleeps == [0.2]


@pytest.mark.parametrize("status", [429, 502, 503, 504])
@respx.mock
async def test_retryable_statuses_are_tried_three_times(status: int) -> None:
    route = respx.post(f"{BASE}/Patient/_search").mock(return_value=httpx.Response(status))
    sleeps: list[float] = []
    async with make_client(sleeps) as client:
        with pytest.raises(FhirError, match=f"HTTP {status}"):
            await client.search("Patient", {})
    assert route.call_count == 3 and sleeps == [0.2, 0.8]


@pytest.mark.parametrize("status", [400, 401, 404, 410, 500])
@respx.mock
async def test_other_errors_are_not_retried(status: int) -> None:
    route = respx.post(f"{BASE}/Patient/_search").mock(return_value=httpx.Response(status))
    sleeps: list[float] = []
    async with make_client(sleeps) as client:
        with pytest.raises(FhirError) as error:
            await client.search("Patient", {})
    assert error.value.status == status and route.call_count == 1 and sleeps == []


@respx.mock
async def test_connection_errors_and_timeouts_are_retried() -> None:
    route = respx.post(f"{BASE}/Patient/_search").mock(
        side_effect=[
            httpx.ConnectError("refused"),
            httpx.ReadTimeout("slow"),
            httpx.Response(200, json=page([])),
        ]
    )
    sleeps: list[float] = []
    async with make_client(sleeps) as client:
        assert await client.search("Patient", {}) == []
    assert route.call_count == 3 and sleeps == [0.2, 0.8]


@respx.mock
async def test_read_fetches_one_resource() -> None:
    respx.get(f"{BASE}/Patient/p1").mock(
        return_value=httpx.Response(200, json={"resourceType": "Patient", "id": "p1"})
    )
    async with make_client([]) as client:
        assert (await client.read("Patient", "p1"))["id"] == "p1"
