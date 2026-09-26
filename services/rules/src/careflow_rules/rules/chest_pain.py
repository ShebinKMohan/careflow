"""CHEST_PAIN_ECG_10MIN (critical): chest pain now or in the last hours.

The deadline is the door-to-ECG standard: `arrived_at` + 10 minutes, or the chest-pain fact's
`recorded_at` + 10 minutes when the arrival time is not known. The rule also asks the patient the
chest-pain questions that are still unanswered.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from careflow_contracts import AlertSeverity, FactKey, NeededFactAudience, RuleId, StructuredFacts
from careflow_rules.engine import RuleContext, RuleOutcome, raise_alert
from careflow_rules.facts import ask, fact_at, is_yes

RULE_ID: Final = RuleId.CHEST_PAIN_ECG_10MIN
DOOR_TO_ECG: Final = timedelta(minutes=10)
FEATURES: Final = (
    FactKey.chest_pain_radiates,
    FactKey.chest_pain_short_of_breath,
    FactKey.chest_pain_sweating,
)
QUESTIONS: Final = (*FEATURES, FactKey.chest_pain_onset)


class ChestPainEcgRule:
    @property
    def id(self) -> str:
        return RULE_ID.value

    def evaluate(self, facts: StructuredFacts, ctx: RuleContext) -> RuleOutcome:
        present = fact_at(facts, FactKey.chest_pain_present)
        if present is None or not is_yes(present):
            return RuleOutcome()
        features = [f.fact_id for key in FEATURES if (f := fact_at(facts, key)) and is_yes(f)]
        deadline = (ctx.arrived_at or present.recorded_at) + DOOR_TO_ECG
        alert = raise_alert(
            facts,
            ctx,
            RULE_ID,
            AlertSeverity.critical,
            [present.fact_id, *features],
            deadline_at=deadline,
        )
        needed = ask(facts, QUESTIONS, NeededFactAudience.patient, "chest_pain_screen")
        return RuleOutcome(alerts=(alert,), needed_facts=needed)
