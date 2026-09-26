"""The rules engine: `evaluate(facts, now)` runs a versioned ruleset over the structured facts.

Safety rule 1: this runs before any urgency suggestion and no model can override its alerts. It
is deterministic: no I/O, no AI, no clock (`now` is passed in) and no randomness.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Protocol

from pydantic import BaseModel, ConfigDict

from careflow_contracts import Alert, AlertSeverity, NeededFact, RuleId, StructuredFacts
from careflow_rules.news2 import (
    NOT_APPLICABLE_UNDER_16,
    News2Result,
    news2_inputs,
    score_news2,
)
from careflow_rules.version import RULESET_VERSION

SEVERITY_ORDER: Final[dict[AlertSeverity, int]] = {
    AlertSeverity.critical: 0,
    AlertSeverity.high: 1,
}


@dataclass(frozen=True, slots=True)
class RuleContext:
    """What every rule sees besides the facts. Rules never read the clock: they use `now`."""

    now: datetime
    ruleset_version: str
    news2: News2Result | None


@dataclass(frozen=True, slots=True)
class RuleOutcome:
    alerts: tuple[Alert, ...] = ()
    needed_facts: tuple[NeededFact, ...] = ()


class Rule(Protocol):
    @property
    def id(self) -> str: ...

    def evaluate(self, facts: StructuredFacts, ctx: RuleContext) -> RuleOutcome: ...


@dataclass(frozen=True, slots=True)
class Ruleset:
    version: str
    rules: Sequence[Rule]


class RulesResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    ruleset_version: str
    alerts: list[Alert]
    needed_facts: list[NeededFact]
    news2: News2Result | None
    #: Why `news2` is None, e.g. "NEWS2 not applicable under 16".
    news2_not_applicable: str | None = None


def alert_id(session_id: str, rule_id: RuleId) -> str:
    """Stable per session and rule, so re-evaluation updates the same alert."""
    digest = hashlib.sha256(f"{session_id}|{rule_id.value}".encode()).hexdigest()[:24]
    return f"alr_{digest}"


def evaluate(facts: StructuredFacts, now: datetime, ruleset: Ruleset | None = None) -> RulesResult:
    """Run every rule in order; dedupe alerts by rule id and needed facts by (key, audience); sort
    alerts by severity (critical first), keeping rule order within a severity."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if ruleset is None:
        from careflow_rules.rules import DEFAULT_RULESET

        ruleset = DEFAULT_RULESET
    inputs = news2_inputs(facts)
    news2 = score_news2(inputs)
    ctx = RuleContext(now=now, ruleset_version=ruleset.version, news2=news2)
    alerts: dict[RuleId, Alert] = {}
    needed: dict[tuple[str, str], NeededFact] = {}
    for rule in ruleset.rules:
        outcome = rule.evaluate(facts, ctx)
        for alert in outcome.alerts:
            alerts.setdefault(alert.rule_id, alert)
        for fact in outcome.needed_facts:
            needed.setdefault((fact.key.value, fact.audience.value), fact)
    return RulesResult(
        ruleset_version=ruleset.version,
        alerts=sorted(alerts.values(), key=lambda alert: SEVERITY_ORDER[alert.severity]),
        needed_facts=list(needed.values()),
        news2=news2,
        news2_not_applicable=NOT_APPLICABLE_UNDER_16 if news2 is None else None,
    )


__all__ = [
    "RULESET_VERSION",
    "Rule",
    "RuleContext",
    "RuleOutcome",
    "Ruleset",
    "RulesResult",
    "alert_id",
    "evaluate",
]
