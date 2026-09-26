"""FactKey lists exactly the leaf fact paths of StructuredFacts."""

from __future__ import annotations

from typing import Any

from careflow_contracts import FactKey
from careflow_contracts.testing import load_schema

TYPED_FACTS = {"TextFact", "IntegerFact", "NumberFact", "TriStateFact", "CodeFact"}


def ref_name(node: dict[str, Any]) -> str | None:
    ref = node.get("$ref")
    return ref.rsplit("/", 1)[-1] if isinstance(ref, str) else None


def is_fact(defs: dict[str, Any], name: str) -> bool:
    if name in TYPED_FACTS:
        return True
    node = defs.get(name, {})
    return any(is_fact(defs, ref_name(part) or "") for part in node.get("allOf", []))


def leaf_paths() -> set[str]:
    schema: dict[str, Any] = load_schema("structured-facts")  # type: ignore[assignment]
    defs: dict[str, Any] = schema["$defs"]
    paths: set[str] = set()

    def walk(properties: dict[str, Any], prefix: str) -> None:
        for key, node in properties.items():
            path = f"{prefix}{key}"
            name = ref_name(node)
            if node.get("type") == "array":
                paths.add(path)  # list-valued paths name the list
            elif name and is_fact(defs, name):
                paths.add(path)
            elif name and "properties" in defs.get(name, {}):
                walk(defs[name]["properties"], f"{path}.")

    walk(defs["StructuredFacts"]["properties"], "")
    return paths


def test_fact_key_matches_the_schema_leaves() -> None:
    assert {key.value for key in FactKey} == leaf_paths()


def test_fact_key_includes_the_names_other_issues_rely_on() -> None:
    values = {key.value for key in FactKey}
    for key in (
        "chest_pain.present",
        "vitals.respiratory_rate",
        "vitals.diastolic_bp",
        "complaint_category",
        "child_danger.appears_ill_to_hcp",
        "child_amber.cap_refill_3s_or_more",
        "infection.rigors",
        "patient.age_years",
    ):
        assert key in values
