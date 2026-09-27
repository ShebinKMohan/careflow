"""The API against its contract, packages/contracts/openapi.yaml (M1-12).

- The app serves every contract path and method, and nothing the contract does not declare
  (the `/v1/dev` hooks only when `CAREFLOW_ENV=dev`).
- Schemathesis generates requests for every operation from the contract, not from FastAPI's own
  schema, and sends them to the real app with its lifespan (intake runtime on Postgres, event
  bus, outbox relay): every response must be a documented status with a documented content type
  and a body that matches its schema, with no server error. Each request carries a token for the
  operation's first `x-careflow-roles` role, bound to the path's session where there is one.

No model is ever called: providers replay an empty cassette, so an extraction fails safe exactly
as it does when the model is down. Needs Postgres and Valkey (`make test-stack` then
`make test-integration`); skipped without them.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any, cast

import pytest
import schemathesis
import yaml
from fastapi import FastAPI
from hypothesis import HealthCheck, settings
from redis import Redis
from sqlalchemy.engine import Engine

from careflow_api.security import Role, create_access_token

REPO = Path(__file__).resolve().parents[4]
CONTRACT = REPO / "packages" / "contracts" / "openapi.yaml"
SPEC: dict[str, Any] = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))


class LateApp:
    """The ASGI app schemathesis sends every request to, including the extra ones its checks
    make (e.g. without auth). Bound when the tests are collected; the module fixture supplies the
    real app, built with this run's settings."""

    def __init__(self) -> None:
        self.app: FastAPI | None = None

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if self.app is None:
            raise RuntimeError("the contract app is not built yet")
        await self.app(scope, receive, send)


LATE_APP = LateApp()
SCHEMA = schemathesis.openapi.from_path(CONTRACT)
SCHEMA.config.update(base_url="http://testserver")
SCHEMA.app = LATE_APP
METHODS = frozenset({"get", "post", "put", "patch", "delete"})
MAX_EXAMPLES = int(os.environ.get("CAREFLOW_CONTRACT_EXAMPLES", "25"))
#: Routes only a phone (a patient token handed over by QR) may use (M2-07, M2-09).
PHONE_ONLY = ("/v1/companion/",)


def contract_operations() -> set[tuple[str, str]]:
    return {
        (path, method)
        for path, item in SPEC["paths"].items()
        for method in item
        if method in METHODS
    }


def served(routes: Sequence[Any]) -> set[tuple[str, str]]:
    """(path, method) for every HTTP route, through FastAPI's lazily included routers."""
    found: set[tuple[str, str]] = set()
    for route in routes:
        candidates = getattr(route, "effective_candidates", None)
        if callable(candidates):  # an included APIRouter (FastAPI 0.14x resolves them lazily)
            found |= served(cast(Sequence[Any], candidates()))
            continue
        methods = getattr(route, "methods", None) or set()
        found |= {(route.path, method.lower()) for method in methods - {"HEAD", "OPTIONS"}}
    return found


def build_app(monkeypatch: pytest.MonkeyPatch, env: str) -> FastAPI:
    from careflow_api import db, hospital, ratelimit
    from careflow_api.config import get_settings
    from careflow_api.main import create_app

    monkeypatch.setenv("CAREFLOW_ENV", env)
    get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.get_sessionmaker.cache_clear()
    hospital._adapter.cache_clear()  # noqa: SLF001 - the FHIR endpoint follows this run's settings
    ratelimit.reset_memory()
    ratelimit.key_prefix = f"cf:contract:{uuid.uuid4().hex}"
    return create_app()


def test_the_app_serves_exactly_the_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    contract = contract_operations()
    assert len(contract) >= 35
    dev = served(build_app(monkeypatch, "dev").routes)
    assert sorted(contract - dev) == [], "declared in the contract but not served"
    assert sorted(dev - contract) == [], "served but not declared in the contract"
    prod = served(build_app(monkeypatch, "prod").routes)
    assert prod == {op for op in contract if not op[0].startswith("/v1/dev/")}


@pytest.fixture(scope="module")
def contract_app(database: Engine) -> Iterator[FastAPI]:
    """The real app, lifespan included, with nothing that could reach a model."""
    from schemathesis.python import asgi

    stream = f"careflow:test:{uuid.uuid4().hex}"
    with pytest.MonkeyPatch.context() as patch:
        cassette = REPO / ".run" / "contract-empty.cassette.json"
        cassette.parent.mkdir(parents=True, exist_ok=True)
        cassette.write_text("{}\n", encoding="utf-8")
        intake = REPO / ".run" / "contract-intake.yaml"
        intake.write_text("llm_phrasing: false\nbriefing_summary: false\n", encoding="utf-8")
        patch.setenv("CAREFLOW_PROVIDER_MODE", "replay")
        patch.setenv("CAREFLOW_CASSETTE", str(cassette))
        patch.setenv("CAREFLOW_INTAKE_CONFIG", str(intake))
        patch.setenv("CAREFLOW_EVENT_STREAM", stream)
        patch.setenv("VITALS_SIM_SPEED", "0")
        app = build_app(patch, "dev")
        LATE_APP.app = app
        yield app
        asgi.shutdown_lifespans()  # the background tasks stop before the database goes
        LATE_APP.app = None
    redis = Redis.from_url(os.environ["REDIS_URL"])
    redis.delete(stream, f"{stream}:dead")
    redis.close()


@pytest.fixture(scope="module")
def token_for(clinics: Any) -> Callable[[Any], dict[str, str]]:
    """Authorization for a case: the operation's first role, bound to the case's session."""
    devices = {"kiosk_device": "kiosk", "board": "board", "integration": "integration"}

    def headers(case: Any) -> dict[str, str]:
        roles: list[Role] = case.operation.definition.raw.get("x-careflow-roles") or []
        if not roles:
            return {}
        role = roles[0]
        extra: dict[str, Any] = {}
        subject = f"contract-{role}"
        if role in devices:
            device_id = str(clinics.devices[("a", devices[role])])
            extra["device_id"], subject = device_id, device_id
        if role == "patient_session":
            session = (case.path_parameters or {}).get("session_id") or str(uuid.uuid4())
            extra["session_id"] = str(session)
            if case.operation.path.startswith(PHONE_ONLY):
                extra["channel"] = "phone"
        token, _ = create_access_token(
            secret=os.environ["JWT_SECRET"],
            subject=subject,
            role=role,
            clinic_id=clinics.a,
            extra=extra,
        )
        return {"Authorization": f"Bearer {token}"}

    return headers


@SCHEMA.parametrize()
@settings(
    max_examples=MAX_EXAMPLES,
    deadline=None,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.filter_too_much,
        HealthCheck.data_too_large,
        HealthCheck.function_scoped_fixture,
    ],
    derandomize=True,
)
def test_every_response_matches_the_contract(
    case: Any, contract_app: FastAPI, token_for: Callable[[Any], dict[str, str]]
) -> None:
    assert LATE_APP.app is contract_app
    case.call_and_validate(headers=token_for(case))
