"""careflow-vitals-sim posts one reading per frame like a device gateway (httpx mocked by respx)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from careflow_api.devices.cli import app
from careflow_contracts.api_models import DeviceVitalsReading

URL = "http://api.test/v1/sessions/ses_1/vitals"
ARGS = [
    "--session",
    "ses_1",
    "--api",
    "http://api.test",
    "--token",
    "secret-device-token",
    "--speed",
    "0",
]


@respx.mock
def test_posts_every_reading_with_the_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIM_BP_CUFF", raising=False)
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, json={"session_id": "ses_1", "fact_ids": []})
    )
    result = CliRunner().invoke(app, [*ARGS, "--scenario", "septic"])
    assert result.exit_code == 0, result.output
    assert route.call_count == 31
    for call in route.calls:
        assert call.request.headers["Authorization"] == "Bearer secret-device-token"
        body = DeviceVitalsReading.model_validate(json.loads(call.request.content))
        assert body.systolic_bp is None and body.heart_rate is not None
    assert "31 readings, 31 accepted (simulated)" in result.output
    assert "secret-device-token" not in result.output


@respx.mock
def test_a_rejected_reading_fails_the_run() -> None:
    responses = [httpx.Response(200, json={"session_id": "ses_1", "fact_ids": []})] * 2
    respx.post(URL).mock(side_effect=[*responses, httpx.Response(422, json={"status": 422})])
    result = CliRunner().invoke(app, [*ARGS, "--scenario", "normal", "--max-readings", "3"])
    assert result.exit_code == 1
    assert "rd_0003 rejected (422)" in result.output


@respx.mock
def test_scenario_falls_back_to_the_env_then_normal(monkeypatch: pytest.MonkeyPatch) -> None:
    respx.post(URL).mock(
        return_value=httpx.Response(200, json={"session_id": "ses_1", "fact_ids": []})
    )
    monkeypatch.setenv("VITALS_SCENARIO", "hypoxic")
    assert "hypoxic, 1 readings" in CliRunner().invoke(app, [*ARGS, "--max-readings", "1"]).output
    monkeypatch.delenv("VITALS_SCENARIO")
    assert "normal, 1 readings" in CliRunner().invoke(app, [*ARGS, "--max-readings", "1"]).output


def test_unknown_scenario_is_a_usage_error() -> None:
    result = CliRunner().invoke(app, [*ARGS, "--scenario", "nope"])
    assert result.exit_code == 2
