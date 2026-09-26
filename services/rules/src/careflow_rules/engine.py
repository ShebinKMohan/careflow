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

from careflow_contracts import (
    Alert,
    AlertSeverity,
    FactKey,
    NeededFact,
    RuleId,
    StructuredFacts,
    render_display_text,
    required_action_for,
)
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
    """What every rule sees besides the facts. Rules never read the clock: they use `now`.

    `arrived_at` is when the patient arrived (the door time for CHEST_PAIN_ECG_10MIN), if known.
    """

    now: datetime
    ruleset_version: str
    news2: News2Result | None
    arrived_at: datetime | None = None


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


def raise_alert(
    facts: StructuredFacts,
    ctx: RuleContext,
    rule_id: RuleId,
    severity: AlertSeverity,
    evidence_fact_ids: Sequence[str],
    *,
    partial: bool = False,
    missing_inputs: Sequence[FactKey] = (),
    qualifiers: Sequence[str] = (),
    deadline_at: datetime | None = None,
) -> Alert:
    """An alert with the fixed template text for `rule_id` in the session language."""
    return Alert(
        alert_id=alert_id(facts.session_id, rule_id),
        session_id=facts.session_id,
        rule_id=rule_id,
        ruleset_version=ctx.ruleset_version,
        severity=severity,
        evidence_fact_ids=list(dict.fromkeys(evidence_fact_ids)),
        required_action=required_action_for(rule_id),
        display_text=render_display_text(rule_id, facts.language.value, qualifiers),
        partial=partial,
        missing_inputs=list(missing_inputs),
        raised_at=ctx.now,
        deadline_at=deadline_at,
    )


def evaluate(
    facts: StructuredFacts,
    now: datetime,
    ruleset: Ruleset | None = None,
    *,
    arrived_at: datetime | None = None,
) -> RulesResult:
    """Run every rule in order; dedupe alerts by rule id and needed facts by (key, audience); sort
    alerts by severity (critical first), keeping rule order within a severity."""
    for name, moment in (("now", now), ("arrived_at", arrived_at)):
        if moment is not None and (moment.tzinfo is None or moment.utcoffset() is None):
            raise ValueError(f"{name} must be timezone-aware")
    if ruleset is None:
        from careflow_rules.rules import DEFAULT_RULESET

        ruleset = DEFAULT_RULESET
    inputs = news2_inputs(facts)
    news2 = score_news2(inputs)
    ctx = RuleContext(now=now, ruleset_version=ruleset.version, news2=news2, arrived_at=arrived_at)
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
    "raise_alert",
    "evaluate",
]
