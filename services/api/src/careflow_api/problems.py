"""Errors as RFC 9457 problem details (application/problem+json). Never echoes request bodies."""

from __future__ import annotations

from http import HTTPStatus

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

PROBLEM_JSON = "application/problem+json"


def problem(status: int, detail: str | None = None, **extra: object) -> JSONResponse:
    body: dict[str, object] = {"type": "about:blank", "title": HTTPStatus(status).phrase}
    body["status"] = status
    if detail:
        body["detail"] = detail
    body.update(extra)
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON)


def install(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_problem(_: Request, exc: HTTPException) -> JSONResponse:
        response = problem(exc.status_code, str(exc.detail) if exc.detail else None)
        for key, value in (exc.headers or {}).items():
            response.headers[key] = value
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_problem(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": ".".join(str(p) for p in error.get("loc", ())), "msg": str(error.get("msg"))}
            for error in exc.errors()
        ]
        return problem(422, "The request failed validation.", errors=errors)
