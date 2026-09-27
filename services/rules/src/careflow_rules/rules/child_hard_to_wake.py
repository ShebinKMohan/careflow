"""CHILD_HARD_TO_WAKE (critical): a child aged 5 to 15 who is hard to wake (M2-04).

NICE NG143 ("Fever in under 5s: assessment and initial management") covers children under 5, where
`NG143_RED_FLAG` raises the same answer. For ages 5 to 15 there is no traffic-light table in this
ruleset (ADR-035), but a drowsy or hard-to-wake child is always urgent, so this rule raises on
`child_danger.hard_to_wake` = yes, and also on "Not sure" (unknown), which carries the fixed
`parent_unsure` qualifier. Age comes from `patient.age_months` or `patient.age_years`
(`careflow_rules.facts.age_months`); without an age, NG143 applies instead.
"""

from __future__ import annotations

from typing import Final

from careflow_contracts import AlertSeverity, FactKey, RuleId, StructuredFacts
from careflow_rules.engine import RuleContext, RuleOutcome, raise_alert
from careflow_rules.facts import age_months, fact_at, is_unsure, is_yes
from careflow_rules.rules.child_ng143 import PARENT_UNSURE, UNDER_5_MONTHS

UNDER_16_MONTHS: Final = 16 * 12


class ChildHardToWakeRule:
    @property
    def id(self) -> str:
        return "CHILD_HARD_TO_WAKE"

    def evaluate(self, facts: StructuredFacts, ctx: RuleContext) -> RuleOutcome:
        known_age = age_months(facts)
        if known_age is None:
            return RuleOutcome()
        age, age_ids = known_age
        if age.low < UNDER_5_MONTHS or age.high >= UNDER_16_MONTHS:
            return RuleOutcome()
        answer = fact_at(facts, FactKey.child_danger_hard_to_wake)
        if not (is_yes(answer) or is_unsure(answer)) or answer is None:
            return RuleOutcome()
        alert = raise_alert(
            facts,
            ctx,
            RuleId.CHILD_HARD_TO_WAKE,
            AlertSeverity.critical,
            [answer.fact_id, *age_ids],
            qualifiers=(PARENT_UNSURE,) if is_unsure(answer) else (),
        )
        return RuleOutcome(alerts=(alert,))
