"""An async FHIR R4 REST client: search with paging, read, timeouts and retries (M1-08).

Only this package talks FHIR. It returns raw resource dicts; `adapters.mapper` parses them.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Final

import httpx

RETRY_STATUSES: Final = frozenset({429, 502, 503, 504})
RETRY_DELAYS: Final = (0.2, 0.8)
MAX_PAGES: Final = 5
PAGE_SIZE: Final = 100
DEFAULT_TIMEOUT: Final = httpx.Timeout(5.0, connect=2.0)

type Json = dict[str, Any]
type Sleep = Callable[[float], Awaitable[None]]


class FhirError(Exception):
    """A FHIR request failed after its retries. The message never contains patient data."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _entries(bundle: Json) -> list[Json]:
    return [entry["resource"] for entry in bundle.get("entry", []) if "resource" in entry]


def _next_link(bundle: Json) -> str | None:
    return next(
        (link.get("url") for link in bundle.get("link", []) if link.get("relation") == "next"),
        None,
    )


class FhirClient:
    """`httpx.AsyncClient` with `Accept: application/fhir+json`. Retries twice (0.2 s, 0.8 s) on
    connection errors, timeouts and HTTP 429/502/503/504; never on any other status."""

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
            headers={"Accept": "application/fhir+json"},
            auth=auth,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> FhirClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _get(self, url: str, params: Mapping[str, str] | None = None) -> Json:
        delays: list[float | None] = [*self._retry_delays, None]
        for delay in delays:
            try:
                response = await self._client.get(url, params=params)
            except httpx.TransportError as exc:  # connection errors and timeouts
                if delay is None:
                    raise FhirError(type(exc).__name__) from exc
                await self._sleep(delay)
                continue
            if response.status_code in RETRY_STATUSES and delay is not None:
                await self._sleep(delay)
                continue
            if response.is_error:
                raise FhirError(f"HTTP {response.status_code}", status=response.status_code)
            body: Json = response.json()
            return body
        raise FhirError(
            "no attempt was made"
        )  # pragma: no cover - the loop always returns or raises

    async def read(self, resource_type: str, resource_id: str) -> Json:
        return await self._get(f"{resource_type}/{resource_id}")

    def _same_origin(self, url: str) -> bool:
        target = httpx.URL(url)
        return (target.scheme, target.host, target.port) == (
            self._origin.scheme,
            self._origin.host,
            self._origin.port,
        )

    async def search(self, resource_type: str, params: Mapping[str, str]) -> list[Json]:
        """Every matching resource, following `Bundle.link[relation=next]` for up to 5 pages of
        100. A next link to another server is refused."""
        bundle = await self._get(resource_type, {**params, "_count": str(PAGE_SIZE)})
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


__all__ = ["DEFAULT_TIMEOUT", "MAX_PAGES", "PAGE_SIZE", "RETRY_STATUSES", "FhirClient", "FhirError"]
