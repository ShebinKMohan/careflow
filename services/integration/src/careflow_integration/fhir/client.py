"""An async FHIR R4 REST client: search with paging, read, transaction, versioned update and
$validate, with timeouts and retries (M1-08, M1-09).

Only this package talks FHIR. It returns raw resource dicts; `adapters.mapper` parses them.
A search sends its parameters in a form body (`POST [type]/_search`), never in the URL: a patient
identifier in a URL ends up in request logs, httpx's, the server's and any proxy's (M2-05).
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

import httpx

RETRY_STATUSES: Final = frozenset({429, 502, 503, 504})
RETRY_DELAYS: Final = (0.2, 0.8)
MAX_PAGES: Final = 5
PAGE_SIZE: Final = 100
DEFAULT_TIMEOUT: Final = httpx.Timeout(5.0, connect=2.0)
FHIR_JSON: Final = "application/fhir+json"
#: $validate answers an invalid resource with its OperationOutcome and one of these statuses.
VALIDATION_STATUSES: Final = frozenset({400, 412, 422})

type Json = dict[str, Any]
type Sleep = Callable[[float], Awaitable[None]]


#: `.../Bundle/123/_history/1` in a Location header.
LOCATION: Final = re.compile(
    r"(?P<type>[A-Z][A-Za-z]+)/(?P<id>[A-Za-z0-9\-.]{1,64})(?:/_history/|/?$)"
)


@dataclass(frozen=True)
class Created:
    """What a create answered: the stored id, whether it was new (201), and the resource."""

    resource_id: str
    created: bool
    body: Json


class FhirError(Exception):
    """A FHIR request failed after its retries. The message never contains patient data."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def canonical_json(body: Json) -> bytes:
    """Sorted keys, no spaces, UTF-8: the same resource always gives the same bytes."""
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _entries(bundle: Json) -> list[Json]:
    return [entry["resource"] for entry in bundle.get("entry", []) if "resource" in entry]


def _next_link(bundle: Json) -> str | None:
    return next(
        (link.get("url") for link in bundle.get("link", []) if link.get("relation") == "next"),
        None,
    )


class FhirClient:
    """`httpx.AsyncClient` with `Accept: application/fhir+json`. Retries twice (0.2 s, 0.8 s) on
    connection errors, timeouts and HTTP 429/502/503/504; never on any other status. A transaction
    is sent once: its caller owns the retries (the write-back handler)."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        retry_delays: Sequence[float] = RETRY_DELAYS,
        auth: httpx.Auth | None = None,  # SMART Backend Services comes in M4-04
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._origin = httpx.URL(self.base_url)
        self._retry_delays = tuple(retry_delays)
        self._sleep = sleep
        self._client = httpx.AsyncClient(
            base_url=self.base_url + "/",
            timeout=timeout,
            headers={"Accept": FHIR_JSON},
            auth=auth,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> FhirClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        body: Json | None = None,
        form: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        retry: bool = True,
        accept: frozenset[int] = frozenset(),
    ) -> Json:
        """One request, retried per the class policy (unless `retry` is False). `body` is sent as
        FHIR JSON, `form` form-encoded. An error status in `accept` returns its body (an
        OperationOutcome) instead of raising."""
        content = None if body is None else canonical_json(body)
        sent = {**(headers or {}), **({"Content-Type": FHIR_JSON} if body is not None else {})}
        delays: list[float | None] = [*self._retry_delays, None] if retry else [None]
        for delay in delays:
            try:
                response = await self._client.request(
                    method, url, params=params, content=content, data=form, headers=sent
                )
            except httpx.TransportError as exc:  # connection errors and timeouts
                if delay is None:
                    raise FhirError(type(exc).__name__) from exc
                await self._sleep(delay)
                continue
            if response.status_code in RETRY_STATUSES and delay is not None:
                await self._sleep(delay)
                continue
            if response.is_error and response.status_code not in accept:
                raise FhirError(f"HTTP {response.status_code}", status=response.status_code)
            answer: Json = response.json()
            return answer
        raise FhirError(
            "no attempt was made"
        )  # pragma: no cover - the loop always returns or raises

    async def _get(self, url: str, params: Mapping[str, str] | None = None) -> Json:
        return await self._request("GET", url, params=params)

    async def read(self, resource_type: str, resource_id: str) -> Json:
        return await self._get(f"{resource_type}/{resource_id}")

    async def transaction(self, bundle: Json) -> Json:
        """POST a transaction Bundle to the base URL, once; returns the transaction-response."""
        return await self._request("POST", "", body=bundle, retry=False)

    async def create(self, resource: Json, *, if_none_exist: str | None = None) -> Created:
        """POST `resource` to its type, once (the caller owns retries). With `if_none_exist` (a
        search such as `identifier=urn:x|1`) it is a conditional create: a server that already
        holds a match answers 200 with it instead of storing a second copy (201)."""
        headers = {"Content-Type": FHIR_JSON, "Prefer": "return=representation"}
        if if_none_exist is not None:
            headers["If-None-Exist"] = if_none_exist
        try:
            response = await self._client.post(
                resource["resourceType"], content=canonical_json(resource), headers=headers
            )
        except httpx.TransportError as exc:
            raise FhirError(type(exc).__name__) from exc
        if response.is_error:
            raise FhirError(f"HTTP {response.status_code}", status=response.status_code)
        body: Json = response.json() if response.content else {}
        found = LOCATION.search(response.headers.get("location", ""))
        resource_id = str(body.get("id") or (found["id"] if found else ""))
        if not resource_id:
            raise FhirError("the server did not say where it stored the resource")
        return Created(resource_id=resource_id, created=response.status_code == 201, body=body)

    async def update(self, resource: Json, *, version_id: str) -> Json:
        """PUT `resource` over version `version_id` only (`If-Match`); HAPI answers 412 when the
        resource changed since. Returns the stored resource."""
        url = f"{resource['resourceType']}/{resource['id']}"
        headers = {"If-Match": f'W/"{version_id}"', "Prefer": "return=representation"}
        return await self._request("PUT", url, body=resource, headers=headers)

    async def validate(self, resource: Json) -> Json:
        """The server's `$validate` OperationOutcome for `resource` (nothing is stored)."""
        url = f"{resource['resourceType']}/$validate"
        return await self._request("POST", url, body=resource, accept=VALIDATION_STATUSES)

    def _same_origin(self, url: str) -> bool:
        target = httpx.URL(url)
        return (target.scheme, target.host, target.port) == (
            self._origin.scheme,
            self._origin.host,
            self._origin.port,
        )

    async def search(self, resource_type: str, params: Mapping[str, str]) -> list[Json]:
        """Every matching resource, following `Bundle.link[relation=next]` for up to 5 pages of
        100. A next link to another server is refused. The parameters go in the body of
        `POST [type]/_search`, never in the URL (a search is a read, so it is retried as one)."""
        form = {**params, "_count": str(PAGE_SIZE)}
        bundle = await self._request("POST", f"{resource_type}/_search", form=form)
        resources = _entries(bundle)
        for _ in range(MAX_PAGES - 1):
            link = _next_link(bundle)
            if link is None:
                break
            if not self._same_origin(link):
                raise FhirError("a next link points outside the FHIR server")
            bundle = await self._get(link)
            resources += _entries(bundle)
        return resources


__all__ = [
    "DEFAULT_TIMEOUT",
    "Created",
    "FHIR_JSON",
    "MAX_PAGES",
    "PAGE_SIZE",
    "RETRY_STATUSES",
    "FhirClient",
    "FhirError",
    "canonical_json",
]
