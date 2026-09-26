"""Properties of the M1-02 rules (Hypothesis, 500 examples each): danger only ever adds alerts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

from hypothesis import given, settings
from hypothesis import strategies as st

from careflow_contracts import FactKey, RuleId
from careflow_rules import evaluate
from careflow_rules.rules.child_ng143 import AMBER_ANSWERS, RED_ANSWERS
from careflow_rules.rules.stroke_fast import FAST_SCREEN_SYMPTOMS, SIGNS
from careflow_rules.testing import facts_from_entries

EXAMPLES: Final = settings(max_examples=500, deadline=None)
NOW: Final = datetime(2026, 9, 26, 9, 5, tzinfo=UTC)
AT: Final = "2026-09-26T09:02:00Z"

type Entry = tuple[str, str, str]  # (path, value, provenance)

YES_FACTS: Final[list[Entry]] = [
    (key.value, "yes", "PATIENT_STATED") for key in (*SIGNS, *RED_ANSWERS, *AMBER_ANSWERS)
] + [
    ("chest_pain.present", "yes", "PATIENT_STATED"),
    ("infection.suspected", "yes", "NURSE_CONFIRMED"),
    ("infection.fever_reported", "yes", "PATIENT_STATED"),
]
VITAL_RANGES: Final = {
    "vitals.heart_rate": (30, 220),
    "vitals.respiratory_rate": (5, 90),
    "vitals.spo2": (70, 100),
    "vitals.systolic_bp": (60, 240),
}


def danger_fact() -> st.SearchStrategy[Entry]:
    vitals = st.sampled_from(sorted(VITAL_RANGES)).flatmap(
        lambda path: st.integers(*VITAL_RANGES[path]).map(
            lambda v: (path, str(v), "DEVICE_MEASURED")
        )
    )
    temperature = st.integers(340, 420).map(
        lambda t: ("vitals.temperature_c", str(t / 10), "DEVICE_MEASURED")
    )
    symptoms = st.sampled_from(sorted(FAST_SCREEN_SYMPTOMS)).map(
        lambda s: ("symptoms", s, "PATIENT_STATED")
    )
    return st.one_of(st.sampled_from(YES_FACTS), vitals, temperature, symptoms)


def age() -> st.SearchStrategy[Entry]:
    return st.one_of(
        st.integers(0, 59).map(lambda m: ("patient.age_months", str(m), "EHR")),
        st.integers(0, 95).map(lambda y: ("patient.age_years", str(y), "EHR")),
    )


def alert_ids(entries: list[Entry]) -> set[RuleId]:
    facts = facts_from_entries(
        [{"path": p, "value": v, "provenance": prov, "recorded_at": AT} for p, v, prov in entries]
    )
    return {alert.rule_id for alert in evaluate(facts, NOW).alerts}


def one_per_path(entries: list[Entry]) -> list[Entry]:
    """Scenario facts hold one current fact per path (symptoms may repeat)."""
    seen: set[str] = set()
    kept: list[Entry] = []
    for entry in entries:
        if entry[0] == "symptoms" or entry[0] not in seen:
            kept.append(entry)
            seen.add(entry[0])
    return kept


@EXAMPLES
@given(age(), st.lists(danger_fact(), max_size=8), danger_fact())
def test_adding_a_danger_fact_never_removes_an_alert(
    patient_age: Entry, base: list[Entry], extra: Entry
) -> None:
    before = one_per_path([patient_age, *base])
    if extra[0] != "symptoms" and extra[0] in {path for path, _, _ in before}:
        return  # the path is taken: adding means a new fact, not changing an answer
    assert alert_ids(before) <= alert_ids([*before, extra])


@EXAMPLES
@given(st.integers(0, 59), st.sampled_from(RED_ANSWERS))
def test_any_single_red_feature_under_5_raises_red(months: int, key: FactKey) -> None:
    path = key.value
    assert RuleId.NG143_RED_FLAG in alert_ids(
        [("patient.age_months", str(months), "EHR"), (path, "yes", "PATIENT_STATED")]
    )


@EXAMPLES
@given(st.integers(0, 59), st.integers(61, 120))
def test_a_respiratory_rate_over_60_under_5_raises_red(months: int, rate: int) -> None:
    entries = [
        ("patient.age_months", str(months), "EHR"),
        ("vitals.respiratory_rate", str(rate), "NURSE_CONFIRMED"),
    ]
    assert RuleId.NG143_RED_FLAG in alert_ids(entries)


@EXAMPLES
@given(age(), st.sampled_from(SIGNS))
def test_any_single_fast_sign_raises_fast_stroke(patient_age: Entry, key: FactKey) -> None:
    path = key.value
    assert RuleId.FAST_STROKE in alert_ids([patient_age, (path, "yes", "PATIENT_STATED")])


@EXAMPLES
@given(
    st.datetimes(
        min_value=datetime(2026, 1, 1), max_value=datetime(2027, 1, 1), timezones=st.just(UTC)
    ),
    st.integers(0, 600),
)
def test_chest_pain_deadline_is_arrival_plus_10_minutes(
    arrived_at: datetime, minutes_later: int
) -> None:
    said = (arrived_at + timedelta(minutes=minutes_later)).isoformat()
    facts = facts_from_entries(
        [
            {
                "path": "chest_pain.present",
                "value": "yes",
                "provenance": "PATIENT_STATED",
                "recorded_at": said,
            }
        ]
    )
    [alert] = evaluate(facts, NOW, arrived_at=arrived_at).alerts
    assert alert.deadline_at is not None
    assert alert.deadline_at - arrived_at == timedelta(minutes=10)
