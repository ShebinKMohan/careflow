"""Errors as RFC 9457 problem details (application/problem+json). Never echoes request bodies."""

from __future__ import annotations

from collections.abc import Iterable
from http import HTTPStatus
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

PROBLEM_JSON = "application/problem+json"


def problem(status: int, detail: str | None = None, **extra: object) -> JSONResponse:
    body: dict[str, object] = {"type": "about:blank", "title": HTTPStatus(status).phrase}
    body["status"] = status
    if detail:
        body["detail"] = detail
    body.update(extra)
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON)


def allowed_methods(routes: Iterable[Any], path: str) -> set[str]:
    """Every method some route serves at `path`. Starlette's 405 lists only the methods of the
    first route that matched the path, and a path's GET and POST are separate routes here."""
    found: set[str] = set()
    for route in routes:
        candidates = getattr(route, "effective_candidates", None)
        if callable(candidates):  # an included APIRouter (FastAPI resolves them lazily)
            found |= allowed_methods(cast(Iterable[Any], candidates()), path)
            continue
        regex, methods = getattr(route, "path_regex", None), getattr(route, "methods", None)
        if regex is not None and methods and regex.match(path):
            found |= set(methods)
    return found


def install(app: FastAPI) -> None:
    # Starlette's HTTPException (FastAPI's is a subclass) also covers routing errors: an unknown
    # route (404) or method (405) is a problem document too.
    @app.exception_handler(HTTPException)
    async def http_problem(request: Request, exc: HTTPException) -> JSONResponse:
        response = problem(exc.status_code, str(exc.detail) if exc.detail else None)
        for key, value in (exc.headers or {}).items():
            response.headers[key] = value
        if exc.status_code == 405 and (methods := allowed_methods(app.routes, request.url.path)):
            response.headers["Allow"] = ", ".join(sorted(methods))  # RFC 9110: all of them
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_problem(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": ".".join(str(p) for p in error.get("loc", ())), "msg": str(error.get("msg"))}
            for error in exc.errors()
        ]
        return problem(422, "The request failed validation.", errors=errors)
