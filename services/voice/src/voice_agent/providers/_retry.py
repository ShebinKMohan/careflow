"""Shared transient-failure retry for streaming HTTP provider calls.

A provider request is retried only while it is being *established* — i.e. before any
response bytes have been handed to the caller. Once the body starts streaming we never
retry (that could double-bill or replay half a spoken reply). Retries fire on connection
errors and on 429/5xx, with capped exponential backoff that honors ``Retry-After``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx

logger = logging.getLogger(__name__)

RETRYABLE_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# Transport-level failures that are safe to retry before any body is read.
RETRYABLE_HTTPX_ERRORS: tuple[type[httpx.HTTPError], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.ReadError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
)

SleepFn = Callable[[float], Awaitable[None]]


def compute_backoff(attempt: int, *, base_seconds: float, max_seconds: float) -> float:
    """Capped exponential backoff: base, 2*base, 4*base, ... clamped to max_seconds."""
    multiplier = 1 << max(attempt - 1, 0)
    return min(base_seconds * multiplier, max_seconds)


def retry_after_seconds(response: httpx.Response, *, max_seconds: float) -> float | None:
    """Parse a delta-seconds ``Retry-After`` header, clamped to max_seconds. HTTP-date
    forms are ignored (treated as absent) so a malformed header never stalls a turn."""
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        seconds = float(raw.strip())
    except ValueError:
        return None
    if seconds < 0:
        return None
    return min(seconds, max_seconds)


@asynccontextmanager
async def stream_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    params: Mapping[str, Any] | None = None,
    json: Any | None = None,
    max_attempts: int,
    backoff_seconds: float,
    max_backoff_seconds: float,
    retryable_statuses: frozenset[int] = RETRYABLE_STATUSES,
    sleep: SleepFn = asyncio.sleep,
) -> AsyncIterator[httpx.Response]:
    """Open a streaming request, retrying establishment on transient failures.

    Yields a successful (2xx) streamed ``httpx.Response`` whose body the caller iterates;
    the response is always closed on exit. On a non-retryable status, or once attempts are
    exhausted, the underlying error is raised exactly as a direct ``raise_for_status()`` /
    transport call would, so callers' error handling is unchanged.
    """
    attempt = 0
    while True:
        attempt += 1
        request = client.build_request(method, url, headers=headers, params=params, json=json)
        try:
            response = await client.send(request, stream=True)
        except RETRYABLE_HTTPX_ERRORS as exc:
            if attempt >= max_attempts:
                raise
            delay = compute_backoff(
                attempt, base_seconds=backoff_seconds, max_seconds=max_backoff_seconds
            )
            logger.warning(
                "Provider request to %s failed (%s); retry %d/%d in %.2fs.",
                url,
                type(exc).__name__,
                attempt,
                max_attempts,
                delay,
            )
            await sleep(delay)
            continue

        if response.is_success:
            try:
                yield response
            finally:
                await response.aclose()
            return

        if attempt < max_attempts and response.status_code in retryable_statuses:
            delay = retry_after_seconds(
                response, max_seconds=max_backoff_seconds
            ) or compute_backoff(
                attempt, base_seconds=backoff_seconds, max_seconds=max_backoff_seconds
            )
            status_code = response.status_code
            await response.aclose()
            logger.warning(
                "Provider request to %s returned HTTP %d; retry %d/%d in %.2fs.",
                url,
                status_code,
                attempt,
                max_attempts,
                delay,
            )
            await sleep(delay)
            continue

        # Not retryable, or attempts exhausted: surface the error like the caller's own
        # raise_for_status() would, then release the connection.
        try:
            response.raise_for_status()
        finally:
            await response.aclose()
        return
