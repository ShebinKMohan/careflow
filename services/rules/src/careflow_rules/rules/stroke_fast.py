"""FAST_STROKE (critical): any FAST sign (face drooping, arm weakness, speech difficulty) is yes.

The alert includes `fast.last_known_well` as evidence when it is known, and asks the patient for it
when it is missing or recorded as unknown. Symptoms that can signal a stroke (FAST_SCREEN_SYMPTOMS)
bring up the FAST screen questions. A negative FAST screen means nothing clinically: the absence
of this alert is never "not a stroke".
"""

from __future__ import annotations

from typing import Final

from careflow_contracts import (
    AlertSeverity,
    FactKey,
    NeededFact,
    NeededFactAudience,
    RuleId,
    StructuredFacts,
    TextFact,
)
from careflow_rules.engine import RuleContext, RuleOutcome, raise_alert
from careflow_rules.facts import fact_at, is_yes, stated_symptoms

RULE_ID: Final = RuleId.FAST_STROKE
SIGNS: Final = (FactKey.fast_face_droop, FactKey.fast_arm_weakness, FactKey.fast_speech_difficulty)
FAST_SCREEN_SYMPTOMS: Final = frozenset(
    {
        "weakness",
        "numbness",
        "confusion",
        "sudden_severe_headache",
        "vision_loss",
        "speech_difficulty",
    }
)
UNKNOWN_TEXT: Final = frozenset({"unknown", "not known", "unsure"})
REASON: Final = "fast_screen"


def last_known_well(facts: StructuredFacts) -> TextFact | None:
    """The onset time, or None when it is missing or recorded as unknown."""
    fact = fact_at(facts, FactKey.fast_last_known_well)
    if isinstance(fact, TextFact) and fact.value.strip().lower() not in UNKNOWN_TEXT:
        return fact
    return None


class FastStrokeRule:
    @property
    def id(self) -> str:
        return RULE_ID.value

    def evaluate(self, facts: StructuredFacts, ctx: RuleContext) -> RuleOutcome:
        signs = [
            fact for key in SIGNS if (fact := fact_at(facts, key)) is not None and is_yes(fact)
        ]
        onset = last_known_well(facts)
        alerts = ()
        wanted: list[FactKey] = []
        if signs:
            evidence = [fact.fact_id for fact in signs] + ([onset.fact_id] if onset else [])
            alerts = (raise_alert(facts, ctx, RULE_ID, AlertSeverity.critical, evidence),)
        if FAST_SCREEN_SYMPTOMS & stated_symptoms(facts).keys():
            wanted += [key for key in SIGNS if fact_at(facts, key) is None]
        if onset is None and (signs or wanted):
            wanted.append(FactKey.fast_last_known_well)
        needed = tuple(
            NeededFact(key=key, audience=NeededFactAudience.patient, reason=REASON)
            for key in dict.fromkeys(wanted)
        )
        return RuleOutcome(alerts=alerts, needed_facts=needed)
