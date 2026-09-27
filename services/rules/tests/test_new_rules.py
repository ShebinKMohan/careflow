"""FAST, chest pain and NICE NG143: edge cases beyond the scenario suite."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

from careflow_contracts import Alert, FactKey, RuleId, StructuredFacts
from careflow_contracts.testing import load_model
from careflow_rules import RULESET_VERSION, evaluate
from careflow_rules.testing import build, facts_from_entries

NOW: Final = datetime(2026, 9, 26, 9, 5, tzinfo=UTC)
ARRIVED: Final = datetime(2026, 9, 26, 9, 0, tzinfo=UTC)


def entries(*items: tuple[str, object, str]) -> StructuredFacts:
    return facts_from_entries(
        [
            {"path": p, "value": str(v), "provenance": prov, "recorded_at": "2026-09-26T09:02:00Z"}
            for p, v, prov in items
        ]
    )


def ids(facts: StructuredFacts, arrived_at: datetime | None = ARRIVED) -> set[str]:
    return {a.rule_id.value for a in evaluate(facts, NOW, arrived_at=arrived_at).alerts}


def test_version_is_bumped() -> None:
    assert RULESET_VERSION == "2026.3.0"


def test_fast_alert_includes_the_onset_as_evidence() -> None:
    facts = entries(
        ("fast.arm_weakness", "yes", "PATIENT_STATED"),
        ("fast.last_known_well", "2026-09-26T08:30:00Z", "PATIENT_STATED"),
    )
    [alert] = evaluate(facts, NOW).alerts
    assert facts.fast is not None and facts.fast.arm_weakness and facts.fast.last_known_well
    assert alert.evidence_fact_ids == [
        facts.fast.arm_weakness.fact_id,
        facts.fast.last_known_well.fact_id,
    ]
    assert alert.required_action == "stroke_team_now"
    assert evaluate(facts, NOW).needed_facts == []


def test_an_onset_recorded_as_unknown_is_asked_for() -> None:
    facts = entries(
        ("fast.face_droop", "yes", "PATIENT_STATED"),
        ("fast.last_known_well", "unknown", "PATIENT_STATED"),
    )
    needed = [n.key for n in evaluate(facts, NOW).needed_facts]
    assert needed == [FactKey.fast_last_known_well]


def test_chest_pain_deadline_without_arrival_uses_the_fact_time() -> None:
    facts = entries(("chest_pain.present", "yes", "PATIENT_STATED"))
    [alert] = evaluate(facts, NOW).alerts
    assert alert.deadline_at == datetime(2026, 9, 26, 9, 12, tzinfo=UTC)
    [alert] = evaluate(facts, NOW, arrived_at=ARRIVED).alerts
    assert alert.deadline_at is not None and alert.deadline_at - ARRIVED == timedelta(minutes=10)


def test_answered_chest_questions_are_not_asked_again() -> None:
    facts = entries(
        ("chest_pain.present", "yes", "PATIENT_STATED"),
        ("chest_pain.radiates", "no", "PATIENT_STATED"),
    )
    needed = [n.key.value for n in evaluate(facts, NOW).needed_facts]
    assert needed == ["chest_pain.short_of_breath", "chest_pain.sweating", "chest_pain.onset"]


def test_ng143_only_under_5() -> None:
    assert ids(
        entries(("patient.age_years", 4, "EHR"), ("child_danger.grunting", "yes", "PATIENT_STATED"))
    ) == {"NG143_RED_FLAG"}
    assert (
        ids(
            entries(
                ("patient.age_years", 5, "EHR"), ("child_danger.grunting", "yes", "PATIENT_STATED")
            )
        )
        == set()
    )
    assert ids(
        entries(("patient.age_months", 59, "EHR"), ("vitals.heart_rate", 150, "DEVICE_MEASURED"))
    ) == {"NG143_AMBER_FLAG"}
    assert (
        ids(
            entries(
                ("patient.age_months", 60, "EHR"), ("vitals.heart_rate", 150, "DEVICE_MEASURED")
            )
        )
        == set()
    )


def test_without_an_age_the_child_screen_answers_still_alert() -> None:
    assert ids(entries(("child_danger.hard_to_wake", "yes", "PATIENT_STATED"))) == {
        "NG143_RED_FLAG"
    }
    # Measured thresholds depend on age, so a fast pulse alone raises nothing without an age.
    assert ids(entries(("vitals.heart_rate", 170, "DEVICE_MEASURED"))) == set()


def test_age_in_years_uses_the_end_that_alerts_sooner() -> None:
    # 0 years may be under 3 months: 38.0 C is red.
    assert "NG143_RED_FLAG" in ids(
        entries(("patient.age_years", 0, "EHR"), ("vitals.temperature_c", 38.0, "DEVICE_MEASURED"))
    )
    # 1 year may be over 12 months: the amber rate limit is 40, and 18 months' 150 pulse limit.
    assert ids(
        entries(("patient.age_years", 1, "EHR"), ("vitals.respiratory_rate", 45, "NURSE_CONFIRMED"))
    ) == {"NG143_AMBER_FLAG"}
    assert ids(
        entries(("patient.age_years", 1, "EHR"), ("vitals.heart_rate", 155, "DEVICE_MEASURED"))
    ) == {"NG143_AMBER_FLAG"}
    # Exact months: 13 months uses 40/min; 12 months still 50/min.
    assert (
        ids(
            entries(
                ("patient.age_months", 12, "EHR"),
                ("vitals.respiratory_rate", 45, "NURSE_CONFIRMED"),
            )
        )
        == set()
    )
    assert ids(
        entries(
            ("patient.age_months", 13, "EHR"), ("vitals.respiratory_rate", 45, "NURSE_CONFIRMED")
        )
    ) == {"NG143_AMBER_FLAG"}


def test_red_and_amber_are_raised_together() -> None:
    facts = entries(
        ("patient.age_months", 18, "EHR"),
        ("vitals.respiratory_rate", 64, "NURSE_CONFIRMED"),
        ("child_amber.crackles", "yes", "NURSE_CONFIRMED"),
    )
    result = evaluate(facts, NOW)
    assert [a.rule_id for a in result.alerts] == [RuleId.NG143_RED_FLAG, RuleId.NG143_AMBER_FLAG]
    assert [a.severity.value for a in result.alerts] == ["critical", "high"]


def test_low_spo2_on_oxygen_is_still_amber() -> None:
    facts = entries(
        ("patient.age_months", 18, "EHR"),
        ("vitals.spo2", 94, "DEVICE_MEASURED"),
        ("vitals.on_supplemental_o2", "yes", "NURSE_CONFIRMED"),
    )
    assert ids(facts) == {"NG143_AMBER_FLAG"}


def test_parent_screen_is_asked_for_a_febrile_or_unwell_child_only() -> None:
    febrile = entries(
        ("patient.age_years", 2, "EHR"), ("infection.fever_reported", "yes", "PATIENT_STATED")
    )
    asked = [n.key.value for n in evaluate(febrile, NOW).needed_facts]
    assert asked == [
        "child_danger.hard_to_wake",
        "child_danger.pale_mottled_ashen_or_blue",
        "child_danger.grunting",
        "child_danger.non_blanching_rash",
        "child_amber.poor_feeding",
        "child_amber.reduced_urine_output",
    ]
    well = entries(("patient.age_years", 2, "EHR"))
    assert evaluate(well, NOW).needed_facts == []


def test_children_get_no_sepsis_news2_even_with_rigors() -> None:
    facts = build(
        vitals={"heart_rate": 190, "respiratory_rate": 70}, infection={"rigors": "yes"}, age=3
    )
    assert "SEPSIS_NEWS2" not in ids(facts)


def test_reproduces_the_contract_child_drowsy_red_flag() -> None:
    """M0-02's drowsy-child example: red for hard to wake; its HR 150 and SpO2 95 are also amber."""
    facts = load_model("valid/child_drowsy.structured-facts.json", StructuredFacts)
    expected = load_model("valid/child_drowsy.alert.json", Alert)
    result = evaluate(facts, NOW)
    red = next(a for a in result.alerts if a.rule_id is RuleId.NG143_RED_FLAG)
    same = ("rule_id", "severity", "required_action", "display_text", "partial")
    assert {k: getattr(red, k) for k in same} == {k: getattr(expected, k) for k in same}
    assert set(expected.evidence_fact_ids) <= set(red.evidence_fact_ids)
    assert {a.rule_id for a in result.alerts} == {RuleId.NG143_RED_FLAG, RuleId.NG143_AMBER_FLAG}
