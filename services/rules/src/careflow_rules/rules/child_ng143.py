"""NICE NG143 red (NG143_RED_FLAG, critical) and amber (NG143_AMBER_FLAG, high) features in
children under 5.

Source: NICE guideline NG143, "Fever in under 5s: assessment and initial management", Table 2
(traffic light system for identifying risk of serious illness, 2013 recommendations), with the
tachycardia thresholds of its Table 1 (APLS). Published 7 November 2019, last updated 26 November
2021; checked item by item on nice.org.uk on 27 September 2026.

Red and amber are raised independently, so adding a feature never removes an alert. Age comes
from `patient.age_months` (exact) or `patient.age_years` (the 12 possible months); each age
threshold uses the end of that range that alerts sooner. Without any age, only the answers of the
child screen apply (they are only asked of children). A parent-reported seizure counts as red:
the kiosk cannot tell status epilepticus or a focal seizure from other fits. SpO2 at or below 95%
is amber in air and also on supplemental oxygen (hypoxic despite oxygen).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

from careflow_contracts import (
    Alert,
    AlertSeverity,
    FactKey,
    NeededFactAudience,
    RuleId,
    StructuredFacts,
)
from careflow_rules.engine import RuleContext, RuleOutcome, raise_alert
from careflow_rules.facts import (
    AgeMonths,
    age_months,
    ask,
    fact_at,
    is_yes,
    one_decimal,
    whole_number,
)

UNDER_5_MONTHS: Final = 60

RED_ANSWERS: Final = (
    FactKey.child_danger_pale_mottled_ashen_or_blue,
    FactKey.child_danger_no_response_to_social_cues,
    FactKey.child_danger_appears_ill_to_hcp,
    FactKey.child_danger_hard_to_wake,
    FactKey.child_danger_weak_high_pitched_or_continuous_cry,
    FactKey.child_danger_grunting,
    FactKey.child_danger_chest_indrawing,
    FactKey.child_danger_reduced_skin_turgor,
    FactKey.child_danger_non_blanching_rash,
    FactKey.child_danger_bulging_fontanelle,
    FactKey.child_danger_neck_stiffness,
    FactKey.child_danger_seizure,
    FactKey.child_danger_focal_neuro_signs,
)
AMBER_ANSWERS: Final = (
    FactKey.child_amber_pallor_reported,
    FactKey.child_amber_not_responding_normally,
    FactKey.child_amber_wakes_only_with_prolonged_stimulation,
    FactKey.child_amber_decreased_activity,
    FactKey.child_amber_nasal_flaring,
    FactKey.child_amber_crackles,
    FactKey.child_amber_cap_refill_3s_or_more,
    FactKey.child_amber_dry_mucous_membranes,
    FactKey.child_amber_poor_feeding,
    FactKey.child_amber_reduced_urine_output,
    FactKey.child_amber_fever_5_days_or_more,
    FactKey.infection_rigors,
    FactKey.child_amber_limb_or_joint_swelling,
    FactKey.child_amber_non_weight_bearing,
)
CHILD_SCREEN: Final = tuple(
    k for k in (*RED_ANSWERS, *AMBER_ANSWERS) if k.value.startswith("child_")
)
PARENT_SCREEN: Final = (
    FactKey.child_danger_hard_to_wake,
    FactKey.child_danger_pale_mottled_ashen_or_blue,
    FactKey.child_danger_grunting,
    FactKey.child_danger_non_blanching_rash,
    FactKey.child_amber_poor_feeding,
    FactKey.child_amber_reduced_urine_output,
)

RED_RESPIRATORY_RATE: Final = 60  # breaths/min, all under-5s ("more than 60")
RED_INFANT_FEVER: Final = Decimal("38.0")  # age < 3 months ("more than or equal to 38 C")
AMBER_YOUNG_INFANT_FEVER: Final = Decimal(
    "39.0"
)  # age 3 to 6 months ("more than or equal to 39 C")
AMBER_SPO2: Final = 95  # percent ("less than or equal to 95%")
FEVER: Final = Decimal("38.0")


def amber_respiratory_limit(age: AgeMonths) -> int | None:
    """More than 50/min at 6 to 12 months, more than 40/min above 12 months; none under 6 months."""
    if age.high > 12:
        return 40
    if age.high >= 6:
        return 50
    return None


def amber_heart_rate_limit(age: AgeMonths) -> int:
    """More than 160/min under 12 months, 150/min at 12 to 24 months, 140/min at 2 to 5 years."""
    if age.high >= 24:
        return 140
    if age.high >= 12:
        return 150
    return 160


class Ng143Rule:
    @property
    def id(self) -> str:
        return "NG143"

    def evaluate(self, facts: StructuredFacts, ctx: RuleContext) -> RuleOutcome:
        known_age = age_months(facts)
        answered = any(fact_at(facts, key) is not None for key in CHILD_SCREEN)
        if known_age is None and not answered:
            return RuleOutcome()
        if known_age is not None and known_age[0].low >= UNDER_5_MONTHS:
            return RuleOutcome()

        red = [f.fact_id for key in RED_ANSWERS if (f := fact_at(facts, key)) and is_yes(f)]
        amber = [f.fact_id for key in AMBER_ANSWERS if (f := fact_at(facts, key)) and is_yes(f)]
        vitals = facts.vitals
        rr = vitals.respiratory_rate if vitals else None
        hr = vitals.heart_rate if vitals else None
        spo2 = vitals.spo2 if vitals else None
        temp = vitals.temperature_c if vitals else None
        if rr is not None and whole_number(rr.value) > RED_RESPIRATORY_RATE:
            red.append(rr.fact_id)
        if spo2 is not None and whole_number(spo2.value) <= AMBER_SPO2:
            amber.append(spo2.fact_id)
        if known_age is not None:
            age, age_ids = known_age
            celsius = one_decimal(temp.value) if temp is not None else None
            if temp is not None and celsius is not None:
                if age.low < 3 and celsius >= RED_INFANT_FEVER:
                    red += [temp.fact_id, *age_ids]
                if age.low <= 6 and age.high >= 3 and celsius >= AMBER_YOUNG_INFANT_FEVER:
                    amber += [temp.fact_id, *age_ids]
            rr_limit = amber_respiratory_limit(age)
            if rr is not None and rr_limit is not None and whole_number(rr.value) > rr_limit:
                amber += [rr.fact_id, *age_ids]
            if hr is not None and whole_number(hr.value) > amber_heart_rate_limit(age):
                amber += [hr.fact_id, *age_ids]

        alerts: list[Alert] = []
        if red:
            alerts.append(
                raise_alert(facts, ctx, RuleId.NG143_RED_FLAG, AlertSeverity.critical, red)
            )
        if amber:
            alerts.append(
                raise_alert(facts, ctx, RuleId.NG143_AMBER_FLAG, AlertSeverity.high, amber)
            )
        needed = ()
        if known_age is not None and self._fever_or_unwell(facts, answered):
            needed = ask(facts, PARENT_SCREEN, NeededFactAudience.patient, "ng143_parent_screen")
        return RuleOutcome(alerts=tuple(alerts), needed_facts=needed)

    @staticmethod
    def _fever_or_unwell(facts: StructuredFacts, answered: bool) -> bool:
        fever = is_yes(fact_at(facts, FactKey.infection_fever_reported))
        temp = facts.vitals.temperature_c if facts.vitals else None
        if temp is not None and one_decimal(temp.value) >= FEVER:
            fever = True
        unwell = facts.chief_complaint is not None or bool(facts.symptoms) or answered
        return fever or unwell
