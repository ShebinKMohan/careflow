"""Test helpers for the contracts: JSON Schema validation and the shared fixtures.

Development only: it needs the `jsonschema` and `referencing` dev dependencies and reads the
schemas and fixtures from the source tree (`packages/contracts/`), so it works in the uv
workspace, not from an installed wheel. Other lanes reuse the persona fixtures through it.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Final, Literal, cast

from jsonschema import Draft202012Validator, ValidationError
from pydantic import BaseModel
from referencing import Resource
from referencing.jsonschema import DRAFT202012, Schema, SchemaRegistry

from careflow_contracts.generated.alert_schema import Alert
from careflow_contracts.generated.nurse_decision_schema import NurseDecision
from careflow_contracts.generated.structured_facts_schema import StructuredFacts
from careflow_contracts.generated.urgency_suggestion_schema import UrgencySuggestion

CONTRACTS_DIR: Final = Path(__file__).resolve().parents[2]
SCHEMAS_DIR: Final = CONTRACTS_DIR / "schemas"
FIXTURES_DIR: Final = CONTRACTS_DIR / "fixtures"

# Fixture files are named `<case>.<schema name>.json`; the schema name picks schema and model.
MODEL_FOR_SCHEMA: Final[dict[str, type[BaseModel]]] = {
    "structured-facts": StructuredFacts,
    "urgency-suggestion": UrgencySuggestion,
    "alert": Alert,
    "nurse-decision": NurseDecision,
}

type Json = dict[str, Json] | list[Json] | str | int | float | bool | None


def load_json(path: Path) -> Json:
    return json.loads(path.read_text(encoding="utf-8"))


@cache
def schema_files() -> dict[str, Path]:
    """Schema name (e.g. `structured-facts`) to its file."""
    return {p.name.removesuffix(".schema.json"): p for p in sorted(SCHEMAS_DIR.glob("*.json"))}


def load_schema(schema_name: str) -> dict[str, Schema]:
    contents: object = json.loads(schema_files()[schema_name].read_text(encoding="utf-8"))
    if not isinstance(contents, dict):
        raise ValueError(f"{schema_name}: a schema must be a JSON object")
    schema = cast(dict[str, Schema], contents)
    if not isinstance(schema.get("$id"), str):
        raise ValueError(f"{schema_name}: a schema needs a string $id")
    return schema


@cache
def registry() -> SchemaRegistry:
    """All schemas, addressable by their `$id`, so cross-file `$ref`s resolve."""
    resources: list[tuple[str, Resource[Schema]]] = []
    for name in schema_files():
        contents = load_schema(name)
        resource = Resource.from_contents(contents, default_specification=DRAFT202012)
        resources.append((str(contents["$id"]), resource))
    return SchemaRegistry().with_resources(resources)


def validator(schema_name: str) -> Draft202012Validator:
    return Draft202012Validator(
        load_schema(schema_name),
        registry=registry(),
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )


def schema_errors(schema_name: str, instance: Json) -> list[str]:
    """JSON Schema errors for `instance`, as short `path: message` lines."""
    errors: list[ValidationError] = list(
        validator(schema_name).iter_errors(instance)  # pyright: ignore[reportUnknownMemberType]
    )
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}" for e in errors]


def schema_name_of(fixture: Path) -> str:
    """`chest_pain.structured-facts.json` -> `structured-facts`."""
    return fixture.name.removesuffix(".json").split(".", 1)[1]


def fixture_paths(kind: Literal["valid", "invalid"]) -> list[Path]:
    return sorted((FIXTURES_DIR / kind).glob("*.json"))


def load_fixture(relative: str) -> Json:
    """Load a fixture by path relative to the fixtures dir, e.g. `valid/stroke.alert.json`."""
    return load_json(FIXTURES_DIR / relative)


def load_model[M: BaseModel](relative: str, model: type[M]) -> M:
    """Load a fixture straight into its Pydantic model."""
    return model.model_validate_json((FIXTURES_DIR / relative).read_text(encoding="utf-8"))
