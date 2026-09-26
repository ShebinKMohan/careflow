"""The ruleset, in evaluation order. M1-02 adds FAST, chest pain and the NICE NG143 child rules."""

from typing import Final

from careflow_rules.engine import Ruleset
from careflow_rules.rules.sepsis import SepsisNews2Rule
from careflow_rules.version import RULESET_VERSION

DEFAULT_RULESET: Final = Ruleset(version=RULESET_VERSION, rules=(SepsisNews2Rule(),))

__all__ = ["DEFAULT_RULESET", "SepsisNews2Rule"]
