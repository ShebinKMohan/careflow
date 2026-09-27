"""The apps' Playwright API fixtures (apps/*/tests/fixtures/api) follow the contract.

The e2e suites stub the API with these files, so a fixture that drifts from openapi.yaml would
test the UI against data the API can never send. Each one is validated like the spec's own
examples: the reference JSON Schema validator against its component schema.
"""

from __future__ import annotations

import json
from functools import cache
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from referencing import Resource
from referencing.jsonschema import DRAFT202012, SchemaRegistry

from careflow_contracts.testing import CONTRACTS_DIR, error_messages, registry

OPENAPI = CONTRACTS_DIR / "openapi.yaml"
APPS = CONTRACTS_DIR.parents[1] / "apps"

#: fixture file (relative to apps/) → the component schema it holds; "[X]" is a list of X, and
#: "frame:X" a live-channel frame {id, type, timestamp, data} whose data is an X.
FIXTURES: dict[str, str] = {
    "kiosk/tests/fixtures/api/checkin.json": "CheckinResult",
    "kiosk/tests/fixtures/api/turns.json": "[TurnResponse]",
    "kiosk/tests/fixtures/api/vitals-status.json": "SessionStatus",
    "kiosk/tests/fixtures/api/done-status.json": "[SessionStatus]",
    "staff/tests/fixtures/api/worklist.json": "Worklist",
    "staff/tests/fixtures/api/triage.json": "TriageView",
    "staff/tests/fixtures/api/triage-after-vitals.json": "TriageView",
    "staff/tests/fixtures/api/facts.json": "StructuredFacts",
    "staff/tests/fixtures/api/me.json": "Me",
    "staff/tests/fixtures/api/decision.json": "DecisionResult",
    "staff/tests/fixtures/api/task-done.json": "Task",
    "staff/tests/fixtures/api/alert-raised.json": "frame:AlertRaisedData",
}


@cache
def spec_registry() -> SchemaRegistry:
    spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    return registry().with_resource(OPENAPI.as_uri(), Resource.from_contents(spec, DRAFT202012))


def validator(schema_name: str) -> Draft202012Validator:
    ref = {"$ref": f"{OPENAPI.as_uri()}#/components/schemas/{schema_name}"}
    return Draft202012Validator(ref, registry=spec_registry())


def test_every_app_fixture_is_listed() -> None:
    found = {path.relative_to(APPS).as_posix() for path in APPS.glob("*/tests/fixtures/api/*.json")}
    assert found == set(FIXTURES)


@pytest.mark.parametrize(("fixture", "schema"), sorted(FIXTURES.items()))
def test_the_fixture_matches_the_contract(fixture: str, schema: str) -> None:
    data: Any = json.loads((APPS / fixture).read_text(encoding="utf-8"))
    if schema.startswith("frame:"):
        assert set(data) == {"id", "type", "timestamp", "data"}, (
            "a live frame is {id, type, timestamp, data}"
        )
        schema, data = schema.removeprefix("frame:"), data["data"]
    items = data if schema.startswith("[") else [data]
    name = schema.strip("[]")
    for item in items:
        assert error_messages(validator(name), item) == [], f"{fixture} is not a valid {name}"


def test_a_broken_fixture_is_caught() -> None:
    worklist = json.loads(
        (APPS / "staff/tests/fixtures/api/worklist.json").read_text(encoding="utf-8")
    )
    worklist[0]["suggested_level"] = 9
    assert error_messages(validator("Worklist"), worklist) != []
