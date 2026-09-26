"""Alert templates: one fixed, non-PHI line per rule and language, at most 160 characters."""

from __future__ import annotations

import re
from typing import Any

import pytest
import yaml

from careflow_contracts import Alert, RuleId, render_display_text, required_action_for
from careflow_contracts.alert_text import DISPLAY_TEXT_MAX
from careflow_contracts.generated.alert_templates import (
    ALERT_QUALIFIERS,
    ALERT_TEMPLATES,
    REQUIRED_ACTIONS,
)
from careflow_contracts.testing import CONTRACTS_DIR, load_model


def source_yaml() -> dict[str, Any]:
    return yaml.safe_load((CONTRACTS_DIR / "alert_templates.yaml").read_text(encoding="utf-8"))


def test_every_rule_has_en_and_ar_templates_and_an_action() -> None:
    assert set(ALERT_TEMPLATES) == {rule.value for rule in RuleId}
    assert set(REQUIRED_ACTIONS) == {rule.value for rule in RuleId}
    for rule_id, langs in ALERT_TEMPLATES.items():
        assert set(langs) == {"en", "ar"}, rule_id
        assert all(0 < len(text) <= DISPLAY_TEXT_MAX for text in langs.values()), rule_id
        assert re.fullmatch(r"[a-z][a-z0-9_]*", REQUIRED_ACTIONS[rule_id])


def test_templates_have_no_placeholders() -> None:
    # display_text is never built from patient words, so templates take no parameters.
    for langs in [*ALERT_TEMPLATES.values(), *ALERT_QUALIFIERS.values()]:
        for text in langs.values():
            assert not re.search(r"[{}%$<>]", text)


def test_generated_module_matches_the_yaml() -> None:
    data = source_yaml()
    assert {k: {"en": v["en"], "ar": v["ar"]} for k, v in data["rules"].items()} == ALERT_TEMPLATES
    assert {k: v["required_action"] for k, v in data["rules"].items()} == REQUIRED_ACTIONS
    assert data["qualifiers"] == ALERT_QUALIFIERS


@pytest.mark.parametrize("rule", list(RuleId))
@pytest.mark.parametrize("language", ["en", "ar"])
def test_template_with_every_qualifier_fits(rule: RuleId, language: str) -> None:
    text = render_display_text(rule, language, ALERT_QUALIFIERS)  # type: ignore[arg-type]
    assert len(text) <= DISPLAY_TEXT_MAX


def test_fixture_alerts_use_the_templates() -> None:
    alert = load_model("valid/sepsis_partial.alert.json", Alert)
    assert alert.display_text == render_display_text(alert.rule_id, "en", ["partial_news2"])
    assert alert.required_action == required_action_for(alert.rule_id)
    for persona in ("chest_pain", "stroke", "child_drowsy"):
        alert = load_model(f"valid/{persona}.alert.json", Alert)
        assert alert.display_text == render_display_text(alert.rule_id)
        assert alert.required_action == required_action_for(alert.rule_id)


def test_unknown_qualifier_is_an_error() -> None:
    with pytest.raises(KeyError):
        render_display_text(RuleId.FAST_STROKE, "en", ["made_up"])
