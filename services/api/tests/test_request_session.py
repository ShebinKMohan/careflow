"""The request's transaction commits before the response is sent (SHE-325).

FastAPI runs a `yield` dependency's exit after the response by default. Injected that way, the
request session committed after the client had its 200, so the nurse's "Seen", then the
worklist's reload at once, could read the token from before the commit (the feels-worse journey
failed on a CI runner). Every route gets the session through `deps.Db`, whose scope is
"function". No database is needed: this reads the routes' dependency trees.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from starlette.routing import BaseRoute

from careflow_api.deps import get_db


def api_routes(routes: Sequence[BaseRoute]) -> Iterator[APIRoute]:
    """Every APIRoute, also inside included routers (FastAPI 0.141 keeps them wrapped, with the
    router they came from as `original_router`)."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from api_routes(inner.routes)


def dependencies(dependant: Dependant) -> Iterator[Dependant]:
    for dependency in dependant.dependencies:
        yield dependency
        yield from dependencies(dependency)


def test_every_request_session_commits_before_the_response(app: FastAPI) -> None:
    uses = [
        (route.path, dependency.scope)
        for route in api_routes(app.routes)
        for dependency in dependencies(route.dependant)
        if dependency.call is get_db
    ]
    paths = {path for path, _ in uses}
    assert "/v1/queue/tokens/{token_id}/close" in paths, "the queue routes take the session"
    assert {scope for _, scope in uses} == {"function"}, uses
