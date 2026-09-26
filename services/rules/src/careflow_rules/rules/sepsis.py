"""SEPSIS_NEWS2: suspected infection with NEWS2 5 or more (critical).

Infection is suspected when any of these holds: `infection.suspected`, `infection.fever_reported`
or `infection.rigors` is yes; a measured temperature (device or nurse) is 38.1 or above or 36.0
or below; or the patient states a symptom from INFECTION_SYMPTOMS. With a partial NEWS2 the alert
is marked partial ("partial NEWS2 (kiosk vitals only)") and the missing NEWS2 inputs become facts
for the nurse to collect.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

from careflow_contracts import (
    AlertSeverity,
    NeededFact,
    NeededFactAudience,
    Provenance,
    RuleId,
    StructuredFacts,
    TriState,
)
from careflow_rules.engine import RuleContext, RuleOutcome, raise_alert
from careflow_rules.facts import MEASURED, one_decimal
from careflow_rules.news2 import PARAMETERS, fact_ids

RULE_ID: Final = RuleId.SEPSIS_NEWS2
NEWS2_THRESHOLD: Final = 5
INFECTION_SYMPTOMS: Final = frozenset({"productive_cough", "dysuria", "wound_redness_or_discharge"})
FEVER_AT_OR_ABOVE: Final = Decimal("38.1")
HYPOTHERMIA_AT_OR_BELOW: Final = Decimal("36.0")
NEEDED_REASON: Final = "news2_incomplete"


def infection_evidence(facts: StructuredFacts) -> list[str]:
    """Fact ids showing a suspected infection, in a fixed order; empty when none is suspected."""
    evidence: list[str] = []
    if facts.infection:
        for fact in (
            facts.infection.suspected,
            facts.infection.fever_reported,
            facts.infection.rigors,
        ):
            if fact is not None and fact.value is TriState.yes:
                evidence.append(fact.fact_id)
    temperature = facts.vitals.temperature_c if facts.vitals else None
    if temperature is not None and temperature.provenance in MEASURED:
        celsius = one_decimal(temperature.value)
        if celsius >= FEVER_AT_OR_ABOVE or celsius <= HYPOTHERMIA_AT_OR_BELOW:
            evidence.append(temperature.fact_id)
    for symptom in facts.symptoms or []:
        name = symptom.name
        if name.provenance is Provenance.PATIENT_STATED and name.value in INFECTION_SYMPTOMS:
            evidence.append(name.fact_id)
    return evidence


class SepsisNews2Rule:
    """Raise SEPSIS_NEWS2 when infection is suspected and NEWS2 is 5 or more."""

    @property
    def id(self) -> str:
        return RULE_ID.value

    def evaluate(self, facts: StructuredFacts, ctx: RuleContext) -> RuleOutcome:
        infection = infection_evidence(facts)
        news2 = ctx.news2
        if not infection or news2 is None:
            return RuleOutcome()
        needed = tuple(
            NeededFact(key=key, audience=NeededFactAudience.nurse, reason=NEEDED_REASON)
            for key in news2.missing
        )
        if news2.total < NEWS2_THRESHOLD:
            return RuleOutcome(needed_facts=needed)
        vitals = fact_ids(facts.vitals)
        contributing = [vitals[p] for p in PARAMETERS if news2.subscores.get(p, 0) > 0]
        alert = raise_alert(
            facts,
            ctx,
            RULE_ID,
            AlertSeverity.critical,
            [*infection, *contributing],
            partial=news2.partial,
            missing_inputs=news2.missing,
            qualifiers=["partial_news2"] if news2.partial else [],
        )
        return RuleOutcome(alerts=(alert,), needed_facts=needed)
