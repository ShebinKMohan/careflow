"""The admin status says what actually runs, not only what is configured (M1-17).

A local model is available only when its server answers; an API one only with its key. A missing
urgency model, or one trained on the synthetic fixture, is "degraded" and named as such.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx

from careflow_api.intake.wiring import PortChoice
from careflow_api.routers import admin
from careflow_providers.config import parse_config

CONFIG = parse_config(
    {
        "tasks": {
            "conversation": {
                "provider": "local",
                "model": "qwen-test",
                "base_url": "http://llm.test/v1",
            },
            "extraction": {
                "provider": "local",
                "model": "qwen-test",
                "base_url": "http://llm.test/v1",
            },
            "summary": {"provider": "api", "vendor": "openai", "model": "gpt-test"},
        }
    }
)


@pytest.fixture(autouse=True)
def providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("careflow_intake.wiring.providers_config", lambda *a, **k: CONFIG)
    monkeypatch.delenv("CAREFLOW_PROVIDER_MODE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def rows(result: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["task"]: row for row in result}


@respx.mock
async def test_a_local_model_is_available_only_when_its_server_answers() -> None:
    probe = respx.get("http://llm.test/v1/models").mock(return_value=httpx.Response(200, json={}))
    up = rows(await admin.provider_rows())
    assert up["extraction"]["available"] is True and "detail" not in up["extraction"]
    assert probe.call_count == 1  # one probe per server, however many tasks use it
    respx.get("http://llm.test/v1/models").mock(side_effect=httpx.ConnectError("refused"))
    down = rows(await admin.provider_rows())
    assert down["extraction"]["available"] is False
    assert down["extraction"]["detail"] == "model server not answering at http://llm.test/v1"
    assert down["summary"] == {
        "task": "summary",
        "provider": "api",
        "model": "gpt-test",
        "available": False,
        "detail": "no API key configured",
    }


def wiring(version: str, *, degraded: bool = False) -> Any:
    choices = (PortChoice("urgency", "trained model (M1-04)", version, degraded=degraded),)
    return SimpleNamespace(urgency_model_version=version, choices=choices)


def test_the_urgency_model_row_says_what_is_installed() -> None:
    assert admin.urgency_row(wiring("none", degraded=True)) == {
        "name": "urgency-model",
        "status": "degraded",
        "detail": "none: no suggestion",
    }
    fixture = admin.urgency_row(wiring("fixture-hgb-1a2b3c"))
    assert fixture["status"] == "degraded"
    assert fixture["detail"].startswith("fixture-hgb-1a2b3c: trained on the synthetic fixture")
    assert "NOT A RESULT" in fixture["detail"]
    assert admin.urgency_row(wiring("ktas-hgb-ebf31b5e-s42")) == {
        "name": "urgency-model",
        "status": "ok",
        "detail": "ktas-hgb-ebf31b5e-s42",
    }
