"""Every example in openapi.yaml validates against its schema (reference jsonschema) and parses into
its generated model in careflow_contracts.api_models. Prism (M0-08) serves these examples."""

from __future__ import annotations

from functools import cache
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from pydantic import TypeAdapter
from referencing import Resource
from referencing.jsonschema import DRAFT202012, SchemaRegistry

from careflow_contracts import api_models
from careflow_contracts.testing import CONTRACTS_DIR, error_messages, registry

OPENAPI = CONTRACTS_DIR / "openapi.yaml"
SPEC: dict[str, Any] = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
METHODS = ("get", "post", "put", "patch", "delete")


def escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


@cache
def spec_registry() -> SchemaRegistry:
    return registry().with_resource(OPENAPI.as_uri(), Resource.from_contents(SPEC, DRAFT202012))


def collect() -> list[tuple[str, str, Any, Any]]:
    """(test id, JSON pointer to the schema, schema, example) for every example in the spec."""
    found: list[tuple[str, str, Any, Any]] = []

    def media(label: str, pointer: str, content: dict[str, Any]) -> None:
        for media_type, body in content.items():
            if "example" in body:
                schema_pointer = f"{pointer}/content/{escape(media_type)}/schema"
                found.append((label, schema_pointer, body["schema"], body["example"]))

    for group in ("paths", "webhooks"):
        for route, item in SPEC[group].items():
            for method in METHODS:
                operation = item.get(method)
                if operation is None:
                    continue
                base = f"#/{group}/{escape(route)}/{method}"
                label = f"{method.upper()} {route}"
                if "requestBody" in operation:
                    body = operation["requestBody"]["content"]
                    media(f"{label} request", f"{base}/requestBody", body)
                for status, response in operation["responses"].items():
                    if "content" in response:
                        media(
                            f"{label} {status}", f"{base}/responses/{status}", response["content"]
                        )
    for name, response in SPEC["components"]["responses"].items():
        media(f"response {name}", f"#/components/responses/{name}", response["content"])
    return found


EXAMPLES = collect()


def model_for(schema: dict[str, Any]) -> Any:
    name = schema["$ref"].rsplit("/", 1)[-1]
    model = getattr(api_models, name, None) or getattr(api_models, f"{name}Model")
    return TypeAdapter(model)


def test_every_response_has_an_example() -> None:
    for route, item in SPEC["paths"].items():
        for method in METHODS:
            if method in item:
                for status, response in item[method]["responses"].items():
                    if "$ref" in response:
                        continue
                    for media_type, body in response.get("content", {}).items():
                        assert "example" in body, f"{method.upper()} {route} {status} {media_type}"


@pytest.mark.parametrize(
    ("label", "pointer", "schema", "example"), EXAMPLES, ids=[e[0] for e in EXAMPLES]
)
def test_example_validates_and_parses(label: str, pointer: str, schema: Any, example: Any) -> None:
    validator = Draft202012Validator(
        {"$ref": f"{OPENAPI.as_uri()}{pointer}"},
        registry=spec_registry(),
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )
    assert error_messages(validator, example) == [], label
    model_for(schema).validate_python(example)


def test_examples_were_found() -> None:
    # 31 routes' success responses, their request bodies, 7 webhooks and the shared error responses.
    assert len(EXAMPLES) >= 50
