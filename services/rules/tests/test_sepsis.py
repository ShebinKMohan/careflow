"""SEPSIS_NEWS2 on the acceptance cases, each infection trigger, and the engine contract."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest

from careflow_contracts import (
    Alert,
    AlertSeverity,
    FactKey,
    NeededFact,
    NeededFactAudience,
    RuleId,
    StructuredFacts,
)
from careflow_contracts.testing import load_model
from careflow_rules import RULESET_VERSION, RuleContext, RuleOutcome, Ruleset, evaluate
from careflow_rules.engine import alert_id
from careflow_rules.testing import build

FIXTURES: Final = Path(__file__).parent / "fixtures"
NOW: Final = datetime(2026, 9, 26, 9, 5, tzinfo=UTC)
NURSE_KEYS: Final = [
    FactKey.vitals_respiratory_rate,
    FactKey.vitals_systolic_bp,
    FactKey.vitals_acvpu,
    FactKey.vitals_on_supplemental_o2,
]
# NEWS2 6 from two parameters, so the infection trigger alone decides.
HIGH_NEWS2: Final = {"heart_rate": 131, "respiratory_rate": 25}


def fixture(name: str) -> StructuredFacts:
    data = json.loads((FIXTURES / f"{name}.structured-facts.json").read_text(encoding="utf-8"))
    return StructuredFacts.model_validate(data)


def sepsis(facts: StructuredFacts) -> Alert | None:
    alerts = [a for a in evaluate(facts, NOW).alerts if a.rule_id is RuleId.SEPSIS_NEWS2]
    return alerts[0] if alerts else None


def test_kiosk_only_case_raises_a_partial_alert_and_asks_the_nurse() -> None:
    """Acceptance: fever + pulse 125, temp 39.4, SpO2 93 -> total 6, partial SEPSIS_NEWS2."""
    facts = fixture("kiosk_sepsis")
    result = evaluate(facts, NOW)
    assert result.news2 is not None
    assert (result.news2.total, result.news2.partial) == (6, True)
    [alert] = result.alerts
    assert alert.rule_id is RuleId.SEPSIS_NEWS2
    assert alert.severity is AlertSeverity.critical
    assert alert.partial is True
    assert alert.missing_inputs == NURSE_KEYS
    assert alert.display_text == (
        "Possible sepsis: suspected infection with NEWS2 5 or more. Urgent review. "
        "(partial NEWS2 (kiosk vitals only))"
    )
    assert alert.required_action == "sepsis_review_now"
    assert alert.ruleset_version == RULESET_VERSION == result.ruleset_version
    assert alert.raised_at == NOW
    vitals = facts.vitals
    infection = facts.infection
    assert vitals is not None and infection is not None and infection.fever_reported is not None
    assert vitals.heart_rate and vitals.temperature_c and vitals.spo2
    assert alert.evidence_fact_ids == [
        infection.fever_reported.fact_id,
        vitals.temperature_c.fact_id,  # 39.4 is also a measured fever
        vitals.spo2.fact_id,
        vitals.heart_rate.fact_id,
    ]
    assert result.needed_facts == [
        NeededFact(key=key, audience=NeededFactAudience.nurse, reason="news2_incomplete")
        for key in NURSE_KEYS
    ]


def test_nurse_vitals_complete_the_score() -> None:
    """Acceptance: adding RR 26, SBP 88, ACVPU V, air -> total 15, band high, partial False."""
    result = evaluate(fixture("nurse_completed"), NOW)
    assert result.news2 is not None
    assert (result.news2.total, result.news2.risk_band, result.news2.partial) == (15, "high", False)
    [alert] = result.alerts
    assert (alert.partial, alert.missing_inputs) == (False, [])
    assert alert.display_text == (
        "Possible sepsis: suspected infection with NEWS2 5 or more. Urgent review."
    )
    assert result.needed_facts == []


def test_benign_case_raises_nothing() -> None:
    """Acceptance: pulse 80, temp 37.0, SpO2 98 -> no alert."""
    result = evaluate(fixture("benign"), NOW)
    assert result.alerts == []
    assert result.needed_facts == []
    assert result.news2 is not None and result.news2.total == 0


def test_reproduces_the_contract_sepsis_partial_alert() -> None:
    """The M0-02 example alert for the sepsis persona, up to its illustrative id and time."""
    facts = load_model("valid/sepsis_partial.structured-facts.json", StructuredFacts)
    expected = load_model("valid/sepsis_partial.alert.json", Alert)
    alert = sepsis(facts)
    assert alert is not None
    same = ("rule_id", "severity", "required_action", "display_text", "partial", "missing_inputs")
    assert {k: getattr(alert, k) for k in same} == {k: getattr(expected, k) for k in same}
    assert set(expected.evidence_fact_ids) <= set(alert.evidence_fact_ids)
    assert alert.session_id == expected.session_id


@pytest.mark.parametrize(
    "kwargs",
    [
        {"infection": {"suspected": "yes"}},
        {"infection": {"fever_reported": "yes"}},
        {"infection": {"rigors": "yes"}},
        {"vitals": {**HIGH_NEWS2, "temperature_c": 38.1}},
        {"vitals": {**HIGH_NEWS2, "temperature_c": 36.0}},
        {"symptoms": ["productive_cough"]},
        {"symptoms": ["dysuria"]},
        {"symptoms": ["wound_redness_or_discharge"]},
    ],
    ids=["suspected", "fever", "rigors", "temp-38.1", "temp-36.0", "cough", "dysuria", "wound"],
)
def test_each_infection_trigger_raises_with_news2_5_or_more(kwargs: dict[str, Any]) -> None:
    kwargs.setdefault("vitals", HIGH_NEWS2)
    assert sepsis(build(**kwargs)) is not None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"infection": {"fever_reported": "no", "rigors": "unknown", "suspected": "no"}},
        {"vitals": {**HIGH_NEWS2, "temperature_c": 38.0}},
        {"vitals": {**HIGH_NEWS2, "temperature_c": 36.1}},
        {"symptoms": ["headache"]},
        {},
    ],
    ids=["answers-no", "temp-38.0", "temp-36.1", "other-symptom", "nothing"],
)
def test_no_infection_suspected_no_alert(kwargs: dict[str, Any]) -> None:
    kwargs.setdefault("vitals", HIGH_NEWS2)
    assert sepsis(build(**kwargs)) is None


def test_a_patient_stated_temperature_is_not_a_measurement() -> None:
    facts = build(vitals=HIGH_NEWS2)
    stated = build(vitals={"temperature_c": 39.5}).model_dump(mode="json")["vitals"][
        "temperature_c"
    ]
    stated["provenance"] = "PATIENT_STATED"
    stated["source"] = {
        "kind": "transcript_span",
        "turn_id": "t1",
        "start": 0,
        "end": 4,
        "quote": "39.5",
    }
    body = facts.model_dump(mode="json", exclude_none=True)
    body["vitals"]["temperature_c"] = stated
    assert sepsis(StructuredFacts.model_validate(body)) is None


def test_news2_4_asks_for_vitals_but_raises_nothing() -> None:
    result = evaluate(build(vitals={"heart_rate": 115, "spo2": 94, "temperature_c": 38.5},
                            infection={"fever_reported": "yes"}), NOW)  # fmt: skip
    assert result.news2 is not None and result.news2.total == 4
    assert result.alerts == []
    assert [n.key for n in result.needed_facts] == NURSE_KEYS


def test_news2_exactly_5_raises() -> None:
    facts = build(vitals={"heart_rate": 115, "spo2": 94, "temperature_c": 39.1},
                  infection={"fever_reported": "yes"})  # fmt: skip
    result = evaluate(facts, NOW)
    assert result.news2 is not None and result.news2.total == 5
    assert sepsis(facts) is not None


def test_children_get_no_news2_and_no_sepsis_news2() -> None:
    result = evaluate(build(vitals=HIGH_NEWS2, infection={"fever_reported": "yes"}, age=12), NOW)
    assert result.news2 is None
    assert result.news2_not_applicable == "NEWS2 not applicable under 16"
    assert result.alerts == []


def test_arabic_session_gets_the_arabic_template() -> None:
    facts = build(vitals=HIGH_NEWS2, infection={"fever_reported": "yes"}, language="ar")
    alert = sepsis(facts)
    assert alert is not None
    assert alert.display_text == (
        "إنتان محتمل: اشتباه بعدوى مع NEWS2 بقيمة 5 أو أكثر. مراجعة عاجلة. "
        "(NEWS2 جزئي (قياسات الكشك فقط))"
    )


def test_alert_ids_are_stable_per_session_and_rule() -> None:
    first = sepsis(fixture("kiosk_sepsis"))
    again = sepsis(fixture("kiosk_sepsis"))
    assert first is not None and again is not None
    assert (
        first.alert_id == again.alert_id == alert_id("ses_rules_kiosk_sepsis", RuleId.SEPSIS_NEWS2)
    )


def test_now_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate(fixture("benign"), datetime(2026, 9, 26, 9, 5))  # noqa: DTZ001


class FixedRule:
    """A stand-in rule that returns fixed alerts, to test the engine's merge and ordering."""

    def __init__(self, rule_id: RuleId, severity: AlertSeverity, marker: str) -> None:
        self.rule_id, self.severity, self.marker = rule_id, severity, marker

    @property
    def id(self) -> str:
        return self.rule_id.value

    def evaluate(self, facts: StructuredFacts, ctx: RuleContext) -> RuleOutcome:
        alert = Alert(
            alert_id=f"alr_{self.marker}",
            session_id=facts.session_id,
            rule_id=self.rule_id,
            ruleset_version=ctx.ruleset_version,
            severity=self.severity,
            evidence_fact_ids=["f_01M3EF2PM000000000035FW001"],
            required_action="test_action",
            partial=False,
            raised_at=ctx.now,
        )
        needed = NeededFact(
            key=FactKey.vitals_acvpu, audience=NeededFactAudience.nurse, reason=self.marker
        )
        return RuleOutcome(alerts=(alert,), needed_facts=(needed,))


def test_engine_dedupes_by_rule_id_and_sorts_by_severity() -> None:
    ruleset = Ruleset(
        version="9.9.9",
        rules=(
            FixedRule(RuleId.NG143_AMBER_FLAG, AlertSeverity.high, "amber"),
            FixedRule(RuleId.FAST_STROKE, AlertSeverity.critical, "stroke-1"),
            FixedRule(RuleId.FAST_STROKE, AlertSeverity.critical, "stroke-2"),
        ),
    )
    result = evaluate(fixture("benign"), NOW, ruleset)
    assert [a.alert_id for a in result.alerts] == ["alr_stroke-1", "alr_amber"]
    assert [n.reason for n in result.needed_facts] == ["amber"]  # same (key, audience) deduped
    assert result.ruleset_version == "9.9.9"
