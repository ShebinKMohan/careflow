"""Hypothesis: pain_score accepts 0-10 and rejects every other integer, in both validators."""

from __future__ import annotations

import copy
from typing import Any

from careflow_contracts import StructuredFacts
from careflow_contracts.testing import load_fixture, schema_errors
from hypothesis import example, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

ROUTINE: dict[str, Any] = load_fixture("valid/routine.structured-facts.json")  # type: ignore[assignment]


def accepted(value: int) -> tuple[bool, bool]:
    doc = copy.deepcopy(ROUTINE)
    doc["pain_score"]["value"] = value
    schema_ok = schema_errors("structured-facts", doc) == []
    try:
        StructuredFacts.model_validate(doc)
        model_ok = True
    except ValidationError:
        model_ok = False
    return schema_ok, model_ok


@settings(max_examples=500, deadline=None)
@given(st.integers())
@example(-1)
@example(0)
@example(10)
@example(11)
def test_pain_score_accepts_only_0_to_10(value: int) -> None:
    expected = 0 <= value <= 10
    assert accepted(value) == (expected, expected)


@given(st.integers(min_value=0, max_value=10))
def test_every_valid_pain_score_is_accepted(value: int) -> None:
    assert accepted(value) == (True, True)
