"""The ruleset, in evaluation order: FAST, chest pain, NICE NG143 (children under 5), a hard-to-wake
child aged 5 to 15, sepsis."""

from typing import Final

from careflow_rules.engine import Ruleset
from careflow_rules.rules.chest_pain import ChestPainEcgRule
from careflow_rules.rules.child_hard_to_wake import ChildHardToWakeRule
from careflow_rules.rules.child_ng143 import Ng143Rule
from careflow_rules.rules.sepsis import SepsisNews2Rule
from careflow_rules.rules.stroke_fast import FastStrokeRule
from careflow_rules.version import RULESET_VERSION

DEFAULT_RULESET: Final = Ruleset(
    version=RULESET_VERSION,
    rules=(
        FastStrokeRule(),
        ChestPainEcgRule(),
        Ng143Rule(),
        ChildHardToWakeRule(),
        SepsisNews2Rule(),
    ),
)

__all__ = [
    "DEFAULT_RULESET",
    "ChestPainEcgRule",
    "ChildHardToWakeRule",
    "FastStrokeRule",
    "Ng143Rule",
    "SepsisNews2Rule",
]
