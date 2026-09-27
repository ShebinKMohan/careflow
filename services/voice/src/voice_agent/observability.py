from __future__ import annotations

import re
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response

REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


async def add_observability_headers(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    request_id = _safe_request_id(request.headers.get("x-request-id"))
    started_at = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    response.headers["x-request-id"] = request_id
    response.headers["x-process-time-ms"] = f"{elapsed_ms:.3f}"
    return response


def _safe_request_id(supplied_request_id: str | None) -> str:
    if supplied_request_id and REQUEST_ID_RE.fullmatch(supplied_request_id):
        return supplied_request_id
    return uuid.uuid4().hex
