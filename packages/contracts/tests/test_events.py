"""Every event type has a data schema and an example that validates against the envelope and its
data schema; event data never carries a name, a date of birth or free text."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from careflow_contracts.events import DATA_MODEL_FOR_EVENT, EventEnvelope, EventType
from careflow_contracts.testing import CONTRACTS_DIR, EVENTS_DIR, error_messages, validator_for_file

SPEC: dict[str, Any] = yaml.safe_load((CONTRACTS_DIR / "openapi.yaml").read_text(encoding="utf-8"))
FORBIDDEN = {"name", "first_name", "last_name", "dob", "birth_date"} | {
    "note",
    "text",
    "quote",
    "message",
}


def data_schema_path(event: EventType) -> Path:
    return EVENTS_DIR / f"{event.value.replace('.', '-').replace('_', '-')}.schema.json"


def data_schema(event: EventType) -> dict[str, Any]:
    doc = json.loads(data_schema_path(event).read_text(encoding="utf-8"))
    return next(v for k, v in doc["$defs"].items() if k.endswith("Data"))


def test_every_event_type_has_a_model_and_a_schema() -> None:
    assert set(DATA_MODEL_FOR_EVENT) == set(EventType)
    for event in EventType:
        assert data_schema_path(event).is_file(), event


@pytest.mark.parametrize("event", list(EventType), ids=lambda e: e.value)
def test_example_validates_against_envelope_and_data(event: EventType) -> None:
    schema = data_schema(event)
    [example] = schema["examples"]
    assert {"clinic_id", "session_id"} <= set(example)
    assert error_messages(validator_for_file(data_schema_path(event)), example) == []
    envelope = {"type": event.value, "timestamp": "2026-09-26T09:05:00Z", "data": example}
    envelope_file = EVENTS_DIR / "envelope.schema.json"
    assert error_messages(validator_for_file(envelope_file), envelope) == []
    EventEnvelope.model_validate(envelope)
    DATA_MODEL_FOR_EVENT[event].model_validate(example)


@pytest.mark.parametrize("event", list(EventType), ids=lambda e: e.value)
def test_webhook_lists_the_event_with_the_same_example(event: EventType) -> None:
    webhook = SPEC["webhooks"][event.value]["post"]["requestBody"]["content"]["application/json"]
    assert webhook["example"]["type"] == event.value
    assert webhook["example"]["data"] == data_schema(event)["examples"][0]


@pytest.mark.parametrize("event", list(EventType), ids=lambda e: e.value)
def test_event_data_has_no_names_dates_of_birth_or_free_text(event: EventType) -> None:
    schema = data_schema(event)
    assert schema["additionalProperties"] is False
    assert not FORBIDDEN & set(schema["properties"])
    assert {"clinic_id", "session_id"} <= set(schema["required"])
