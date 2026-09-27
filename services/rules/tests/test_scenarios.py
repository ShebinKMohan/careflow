"""The YAML scenario suite (eval/scenarios/rules): danger answers alert, benign cases do not."""

from __future__ import annotations

import json
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any, Final

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker

from careflow_contracts import RuleId
from careflow_contracts.testing import error_messages
from careflow_rules import evaluate
from careflow_rules.rules.child_ng143 import AMBER_ANSWERS, RED_ANSWERS
from careflow_rules.testing import facts_from_entries

SCENARIOS_DIR: Final = Path(__file__).resolve().parents[3] / "eval" / "scenarios" / "rules"
# child/: the generated M2-04 answer scenarios (scripts/child_scenarios.py, SHE-293).
SCENARIOS: Final = sorted([*SCENARIOS_DIR.glob("*.yaml"), *SCENARIOS_DIR.glob("child/*.yaml")])
MEASURED_RED: Final = ("vitals.respiratory_rate", "vitals.temperature_c")
MEASURED_AMBER: Final = (
    "vitals.respiratory_rate",
    "vitals.spo2",
    "vitals.heart_rate",
    "vitals.temperature_c",
)


@cache
def validator() -> Draft202012Validator:
    schema = json.loads((SCENARIOS_DIR / "_schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data


def when(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def run(scenario: dict[str, Any]) -> Any:
    entries = [{k: str(v) for k, v in entry.items()} for entry in scenario["facts"]]
    facts = facts_from_entries(
        entries, session_id=f"ses_{scenario['id']}", language=scenario.get("language", "en")
    )
    now = when(scenario["now"])
    assert now is not None
    return evaluate(facts, now, arrived_at=when(scenario["arrived_at"]))


def test_the_suite_has_at_least_45_scenarios_and_10_benign() -> None:
    assert len(SCENARIOS) >= 45
    assert sum(1 for p in SCENARIOS if not load(p)["expect_alerts"]) >= 10


def test_the_child_answer_scenarios_are_all_there() -> None:
    """M2-04: 16 questions x yes/unsure x 3 ages, and 5 all-no controls."""
    child = [p for p in SCENARIOS if p.parent.name == "child"]
    assert len(child) == 16 * 2 * 3 + 5
    assert sum(1 for p in child if not load(p)["expect_alerts"]) >= 5


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_scenario(path: Path) -> None:
    scenario = load(path)
    errors = error_messages(validator(), scenario)
    assert errors == []
    assert scenario["id"] == path.stem
    result = run(scenario)
    assert {a.rule_id.value for a in result.alerts} == set(scenario["expect_alerts"])
    needed = {n.key.value for n in result.needed_facts}
    assert set(scenario.get("expect_needed", [])) <= needed
    if "expect_deadline_at" in scenario:
        [chest] = [a for a in result.alerts if a.rule_id is RuleId.CHEST_PAIN_ECG_10MIN]
        assert chest.deadline_at == when(scenario["expect_deadline_at"])


def covered(flag: str) -> set[str]:
    """Features named in `covers` by scenarios that expect the given alert."""
    kind = "red" if flag == "NG143_RED_FLAG" else "amber"
    found: set[str] = set()
    for path in SCENARIOS:
        scenario = load(path)
        if flag in scenario["expect_alerts"]:
            found |= {
                c.split(":", 1)[1] for c in scenario.get("covers", []) if c.startswith(f"{kind}:")
            }
    return found


def test_every_red_feature_has_its_own_scenario() -> None:
    expected = {key.value for key in RED_ANSWERS} | set(MEASURED_RED)
    assert expected <= covered("NG143_RED_FLAG"), expected - covered("NG143_RED_FLAG")


def test_every_amber_feature_has_its_own_scenario() -> None:
    expected = {key.value for key in AMBER_ANSWERS} | set(MEASURED_AMBER)
    assert expected <= covered("NG143_AMBER_FLAG"), expected - covered("NG143_AMBER_FLAG")


def test_single_feature_scenarios_raise_only_their_flag() -> None:
    for path in SCENARIOS:
        scenario = load(path)
        covers = scenario.get("covers", [])
        if any(c.startswith(("red:", "amber:")) for c in covers):
            assert len(scenario["expect_alerts"]) == 1, path.stem
