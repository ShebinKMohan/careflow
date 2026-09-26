"""CareFlow safety rules engine: pure Python, no I/O, no AI.

`evaluate(facts, now)` returns the alerts raised, the facts still needed and the NEWS2 score.
Research prototype. Synthetic data only. Not for clinical use.
"""

from careflow_rules.engine import Rule, RuleContext, RuleOutcome, Ruleset, RulesResult, evaluate
from careflow_rules.news2 import News2Inputs, News2Result, merge_vitals, score_news2
from careflow_rules.rules import DEFAULT_RULESET
from careflow_rules.version import RULESET_VERSION

__version__ = RULESET_VERSION

__all__ = [
    "DEFAULT_RULESET",
    "RULESET_VERSION",
    "News2Inputs",
    "News2Result",
    "Rule",
    "RuleContext",
    "RuleOutcome",
    "Ruleset",
    "RulesResult",
    "evaluate",
    "merge_vitals",
    "score_news2",
]
