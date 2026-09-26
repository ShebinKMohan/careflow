"""Fixtures: the recorded HAPI answers for the 7 synthetic personas, replayed with respx."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from careflow_integration.adapters.loader import HospitalAdapter, load_adapter

FIXTURES = Path(__file__).parent / "fixtures" / "fhir"
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)  # the day the persona answers were recorded
BASE = "http://fhir.test/fhir"

type Answers = dict[str, list[dict[str, Any]]]


def hapi_answers(base_url: str) -> bool:
    try:
        return httpx.get(f"{base_url}/metadata", timeout=3.0).is_success
    except httpx.HTTPError:
        return False


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    marked = [item for item in items if "hapi" in item.keywords]
    base = load_adapter().fhir_base_url
    if not marked or hapi_answers(base):
        return
    skip = pytest.mark.skip(reason=f"hapi: HAPI FHIR is not answering at {base} (run `make up`)")
    for item in marked:
        item.add_marker(skip)


def load_answers(persona: str) -> Answers:
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in (FIXTURES / persona).glob("*.json")
    }


@pytest.fixture
def recorded() -> Callable[[str], Answers]:
    """The resources HAPI returned for a persona, by resource type (record.py)."""
    return load_answers


@pytest.fixture
def now() -> datetime:
    return NOW


def bundle(resources: list[dict[str, Any]], next_url: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": len(resources),
        "entry": [{"resource": resource} for resource in resources],
    }
    if next_url:
        body["link"] = [{"relation": "next", "url": next_url}]
    return body


@pytest.fixture
def adapter() -> HospitalAdapter:
    return load_adapter().model_copy(update={"fhir_base_url": BASE})


@pytest.fixture
def serve() -> Callable[..., respx.MockRouter]:
    """Route every FHIR search to `answers` (by resource type); `fail` maps types to a status."""

    def make(
        router: respx.MockRouter, answers: Answers, fail: dict[str, int] | None = None
    ) -> respx.MockRouter:
        failures = fail or {}

        def handler(request: httpx.Request) -> httpx.Response:
            kind = request.url.path.rsplit("/", 1)[-1]
            if kind in failures:
                return httpx.Response(failures[kind])
            return httpx.Response(200, json=bundle(answers.get(kind, [])))

        router.get(url__startswith=BASE).mock(side_effect=handler)
        return router

    return make
