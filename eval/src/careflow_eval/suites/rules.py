"""The established synthetic rules scenarios, evaluated without a language model."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from careflow_eval.providers import canonical_hash
from careflow_rules import evaluate
from careflow_rules.testing import facts_from_entries


def run(directory: Path) -> tuple[list[dict[str, Any]], str]:
    paths = sorted(directory.glob("*.yaml"))
    if not paths:
        raise ValueError(f"no rule scenarios in {directory}")
    validator = Draft202012Validator(
        json.loads((directory / "_schema.json").read_text()), format_checker=FormatChecker()
    )
    dataset_sha256 = canonical_hash({p.name: p.read_text(encoding="utf-8") for p in paths})
    results: list[dict[str, Any]] = []
    for path in paths:
        scenario = yaml.safe_load(path.read_text(encoding="utf-8"))
        validator.validate(scenario)
        if scenario["id"] != path.stem:
            raise ValueError(f"scenario id differs from filename: {path.name}")
        facts = facts_from_entries(
            [{key: str(value) for key, value in entry.items()} for entry in scenario["facts"]],
            session_id=f"ses_{scenario['id']}",
            language=scenario.get("language", "en"),
        )
        started = time.perf_counter()
        outcome = evaluate(
            facts,
            datetime.fromisoformat(scenario["now"]),
            arrived_at=datetime.fromisoformat(scenario["arrived_at"])
            if scenario["arrived_at"] is not None
            else None,
        )
        duration_ms = (time.perf_counter() - started) * 1000
        alerts = sorted(alert.rule_id.value for alert in outcome.alerts)
        needed = sorted(need.key.value for need in outcome.needed_facts)
        problems = []
        if set(alerts) != set(scenario["expect_alerts"]):
            problems.append("alerts differ")
        if not set(scenario.get("expect_needed", [])) <= set(needed):
            problems.append("expected needed facts missing")
        if "expect_deadline_at" in scenario:
            deadlines = [
                a.deadline_at for a in outcome.alerts if a.rule_id == "CHEST_PAIN_ECG_10MIN"
            ]
            if deadlines != [datetime.fromisoformat(scenario["expect_deadline_at"])]:
                problems.append("ECG deadline differs")
        results.append(
            {
                "id": scenario["id"],
                "status": "completed",
                "rules_validation": not problems,
                "problems": problems,
                "prediction": {
                    "facts": [],
                    "alerts": alerts,
                    "latency_ms": duration_ms,
                    "extraction_incomplete": False,
                    "dropped_count": 0,
                },
                "expected": {"facts": [], "alerts": sorted(scenario["expect_alerts"])},
                "needed_facts": needed,
                "latency_source": "measured_rules_execution",
            }
        )
    return results, dataset_sha256
