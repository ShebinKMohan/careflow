"""The dev-only simulator route: 404 unless CAREFLOW_ENV=dev; kiosk devices only when enabled."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from careflow_api.config import get_settings
from careflow_api.devices.gateway import latest_vitals
from careflow_api.main import create_app
from careflow_api.security import Role, create_access_token
from careflow_api.telemetry import build_tracer_provider

ROUTE = "/v1/dev/sessions/ses_dev_1/vitals-sim"


def bearer(role: Role) -> dict[str, str]:
    token, _ = create_access_token(
        secret=os.environ["JWT_SECRET"], subject=f"sub-{role}", role=role, clinic_id=uuid.uuid4(),
        extra={"device_id": "kiosk_test"},
    )  # fmt: skip
    return {"Authorization": f"Bearer {token}"}


async def client_for(env: str | None, monkeypatch: pytest.MonkeyPatch) -> AsyncClient:
    if env is None:
        monkeypatch.delenv("CAREFLOW_ENV", raising=False)
    else:
        monkeypatch.setenv("CAREFLOW_ENV", env)
    monkeypatch.setenv("VITALS_SIM_SPEED", "0")
    get_settings.cache_clear()
    app = create_app(tracer_provider=build_tracer_provider(exporter=InMemorySpanExporter()))
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://api.test")


@pytest.fixture(autouse=True)
def reset() -> Iterator[None]:
    latest_vitals.clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize("env", [None, "prod", "test", "DEV"])
async def test_404_unless_the_env_is_dev(monkeypatch: pytest.MonkeyPatch, env: str | None) -> None:
    async with await client_for(env, monkeypatch) as http:
        response = await http.post(
            ROUTE, json={"scenario": "hypoxic"}, headers=bearer("kiosk_device")
        )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_dev_runs_the_simulator_for_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    async with await client_for("dev", monkeypatch) as http:
        response = await http.post(
            ROUTE, json={"scenario": "hypoxic"}, headers=bearer("kiosk_device")
        )
    assert response.status_code == 202
    assert response.json() == {
        "session_id": "ses_dev_1",
        "scenario": "hypoxic",
        "frames": 31,
        "simulated": True,
    }
    latest = latest_vitals.get("ses_dev_1")  # the background task has run (speed 0)
    assert latest is not None and latest.simulated and latest.spo2 is not None and latest.spo2 <= 89


async def test_dev_scenario_order_body_then_env_then_normal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with await client_for("dev", monkeypatch) as http:
        monkeypatch.setenv("VITALS_SCENARIO", "septic")
        assert (await http.post(ROUTE, headers=bearer("kiosk_device"))).json()[
            "scenario"
        ] == "septic"
        monkeypatch.delenv("VITALS_SCENARIO")
        assert (await http.post(ROUTE, headers=bearer("kiosk_device"))).json()[
            "scenario"
        ] == "normal"
        bad = await http.post(ROUTE, json={"scenario": "unknown"}, headers=bearer("kiosk_device"))
        assert bad.status_code == 422


async def test_dev_route_is_for_kiosk_devices_only(monkeypatch: pytest.MonkeyPatch) -> None:
    async with await client_for("dev", monkeypatch) as http:
        assert (await http.post(ROUTE, json={})).status_code == 401
        assert (await http.post(ROUTE, json={}, headers=bearer("nurse"))).status_code == 403
