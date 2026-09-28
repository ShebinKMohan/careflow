"""The fixed child question bank (M2-04), asked after the parent's opening description.

The questions are the NICE NG143 ("Fever in under 5s: assessment and initial management")
traffic-light features a parent can answer at a kiosk: the red ones first, then the amber ones.
Each maps to its exact `child_danger.*` / `child_amber.*` / `infection.*` fact (M0-02); the
thresholds and the alerts stay in the rules engine. The wording is the approved template
(`child_questions` in templates_*.yaml), asked verbatim: never rephrased by a model. A question
is asked again once if its answer gave no fact; the bank is never cut short by the intake caps.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Final

from careflow_contracts.generated.structured_facts_schema import (
    FactKey,
    StructuredFacts,
    Turn,
    TurnRole,
)
from careflow_intake.conversation.picker import answered_keys

CHILD_RED: Final = (
    FactKey.child_danger_hard_to_wake,
    FactKey.child_danger_no_response_to_social_cues,
    FactKey.child_danger_weak_high_pitched_or_continuous_cry,
    FactKey.child_danger_pale_mottled_ashen_or_blue,
    FactKey.child_danger_grunting,
    FactKey.child_danger_non_blanching_rash,
    FactKey.child_danger_neck_stiffness,
    FactKey.child_danger_seizure,
)
CHILD_AMBER: Final = (
    FactKey.child_amber_decreased_activity,
    FactKey.child_amber_not_responding_normally,
    FactKey.child_amber_poor_feeding,
    FactKey.child_amber_reduced_urine_output,
    FactKey.child_amber_fever_5_days_or_more,
    FactKey.infection_rigors,
    FactKey.child_amber_limb_or_joint_swelling,
    FactKey.child_amber_non_weight_bearing,
)
CHILD_BANK: Final = (*CHILD_RED, *CHILD_AMBER)
MAX_ASKS: Final = 2


def asked_counts(turns: Sequence[Turn]) -> Counter[FactKey]:
    """How many times each key has been asked."""
    return Counter(
        turn.question_key
        for turn in turns
        if turn.role is TurnRole.assistant and turn.question_key is not None
    )


def next_child_question(facts: StructuredFacts, asked: Mapping[FactKey, int]) -> FactKey | None:
    """The first bank question without an answer that has been asked fewer than twice."""
    answered = answered_keys(facts)
    for key in CHILD_BANK:
        if key not in answered and asked.get(key, 0) < MAX_ASKS:
            return key
    return None


__all__ = [
    "CHILD_AMBER",
    "CHILD_BANK",
    "CHILD_RED",
    "MAX_ASKS",
    "asked_counts",
    "next_child_question",
]
