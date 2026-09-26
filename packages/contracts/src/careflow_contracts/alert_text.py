"""Render the fixed, non-PHI alert line for a rule.

`Alert.display_text` is never built from patient words: it is the template for the rule id, in
the session language, plus optional fixed qualifiers (e.g. "partial NEWS2 (kiosk vitals only)").
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final, Literal

from careflow_contracts.generated.alert_schema import RuleId
from careflow_contracts.generated.alert_templates import (
    ALERT_QUALIFIERS,
    ALERT_TEMPLATES,
    REQUIRED_ACTIONS,
)

DISPLAY_TEXT_MAX: Final = 160

type AlertLanguage = Literal["en", "ar"]


def required_action_for(rule_id: RuleId) -> str:
    """The message key of the action a rule requires, e.g. `ecg_within_10_min`."""
    return REQUIRED_ACTIONS[rule_id.value]


def render_display_text(
    rule_id: RuleId, language: AlertLanguage = "en", qualifiers: Iterable[str] = ()
) -> str:
    """Template for `rule_id` plus fixed qualifiers in brackets, at most 160 characters.

    Raises KeyError for an unknown qualifier key and ValueError if the line would be too long.
    """
    text = ALERT_TEMPLATES[rule_id.value][language]
    extras = [ALERT_QUALIFIERS[key][language] for key in qualifiers]
    if extras:
        text = f"{text} ({'; '.join(extras)})"
    if len(text) > DISPLAY_TEXT_MAX:
        raise ValueError(f"display_text for {rule_id.value} is {len(text)} > {DISPLAY_TEXT_MAX}")
    return text
