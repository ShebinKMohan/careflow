"""NEWS2 tables (Royal College of Physicians, NEWS2 (2017)): both sides of every boundary."""

from __future__ import annotations

import pytest

from careflow_contracts import AcvpuCode, FactKey
from careflow_rules import News2Inputs, score_news2
from careflow_rules.news2 import (
    risk_band,
    score_consciousness,
    score_pulse,
    score_respiratory_rate,
    score_spo2_scale1,
    score_supplemental_o2,
    score_systolic_bp,
    score_temperature,
)


@pytest.mark.parametrize(
    ("rate", "score"),
    [(0, 3), (8, 3), (8.4, 3), (8.5, 1), (9, 1), (11, 1), (11.5, 0), (12, 0), (20, 0), (20.5, 2),
     (21, 2), (24, 2), (24.5, 3), (25, 3), (60, 3)],
)  # fmt: skip
def test_respiratory_rate(rate: float, score: int) -> None:
    assert score_respiratory_rate(rate) == score


@pytest.mark.parametrize(
    ("spo2", "score"),
    [(70, 3), (91, 3), (91.4, 3), (91.5, 2), (92, 2), (93, 2), (94, 1), (95, 1), (95.5, 0), (96, 0),
     (100, 0)],
)  # fmt: skip
def test_spo2_scale_1(spo2: float, score: int) -> None:
    assert score_spo2_scale1(spo2) == score


@pytest.mark.parametrize(("on_oxygen", "score"), [(True, 2), (False, 0)])
def test_air_or_oxygen(on_oxygen: bool, score: int) -> None:
    assert score_supplemental_o2(on_oxygen) == score


@pytest.mark.parametrize(
    ("systolic", "score"),
    [(50, 3), (90, 3), (90.5, 2), (91, 2), (100, 2), (101, 1), (110, 1), (111, 0), (219, 0),
     (219.5, 3), (220, 3), (260, 3)],
)  # fmt: skip
def test_systolic_bp(systolic: float, score: int) -> None:
    assert score_systolic_bp(systolic) == score


@pytest.mark.parametrize(
    ("pulse", "score"),
    [(30, 3), (40, 3), (40.5, 1), (41, 1), (50, 1), (51, 0), (90, 0), (91, 1), (110, 1), (111, 2),
     (130, 2), (130.5, 3), (131, 3), (200, 3)],
)  # fmt: skip
def test_pulse(pulse: float, score: int) -> None:
    assert score_pulse(pulse) == score


@pytest.mark.parametrize(
    ("level", "score"),
    [(AcvpuCode.A, 0), (AcvpuCode.C, 3), (AcvpuCode.V, 3), (AcvpuCode.P, 3), (AcvpuCode.U, 3)],
)
def test_consciousness(level: AcvpuCode, score: int) -> None:
    assert score_consciousness(level) == score


@pytest.mark.parametrize(
    ("celsius", "score"),
    [(33.0, 3), (35.0, 3), (35.04, 3), (35.05, 1), (35.1, 1), (36.0, 1), (36.04, 1), (36.05, 0),
     (36.1, 0), (38.0, 0), (38.04, 0), (38.05, 1), (38.1, 1), (39.0, 1), (39.05, 2), (39.1, 2),
     (41.0, 2)],
)  # fmt: skip
def test_temperature(celsius: float, score: int) -> None:
    assert score_temperature(celsius) == score


@pytest.mark.parametrize(
    ("total", "single_3", "band"),
    [(0, False, "low"), (3, True, "low-medium"), (4, False, "low"), (4, True, "low-medium"),
     (5, False, "medium"), (6, True, "medium"), (7, False, "high"), (20, True, "high")],
)  # fmt: skip
def test_risk_band(total: int, single_3: bool, band: str) -> None:
    assert risk_band(total, single_3) == band


def test_kiosk_vitals_only_is_partial() -> None:
    """Acceptance: pulse 125, temp 39.4, SpO2 93 -> total 6 from kiosk vitals only."""
    result = score_news2(News2Inputs(heart_rate=125, temperature_c=39.4, spo2=93))
    assert result is not None
    assert (result.total, result.risk_band, result.partial) == (6, "medium", True)
    assert result.subscores == {"spo2": 2, "heart_rate": 2, "temperature_c": 2}
    assert result.missing == [
        FactKey.vitals_respiratory_rate,
        FactKey.vitals_systolic_bp,
        FactKey.vitals_acvpu,
        FactKey.vitals_on_supplemental_o2,
    ]
    assert result.any_single_3 is False


def test_full_set_after_the_nurse() -> None:
    """Acceptance: adding RR 26, SBP 88, ACVPU V, air -> total 15, band high, not partial."""
    inputs = News2Inputs(
        heart_rate=125,
        temperature_c=39.4,
        spo2=93,
        respiratory_rate=26,
        systolic_bp=88,
        acvpu=AcvpuCode.V,
        on_supplemental_o2=False,
    )
    result = score_news2(inputs)
    assert result is not None
    assert (result.total, result.risk_band, result.partial, result.missing) == (
        15,
        "high",
        False,
        [],
    )
    assert result.any_single_3 is True


def test_single_3_with_low_total_is_low_medium() -> None:
    result = score_news2(News2Inputs(respiratory_rate=26))
    assert result is not None
    assert (result.total, result.risk_band) == (3, "low-medium")


def test_nothing_measured_scores_zero_and_is_partial() -> None:
    result = score_news2(News2Inputs())
    assert result is not None
    assert (result.total, result.partial, len(result.missing)) == (0, True, 7)


@pytest.mark.parametrize(("age", "applicable"), [(15, False), (16, True), (70, True), (None, True)])
def test_not_applicable_under_16(age: int | None, applicable: bool) -> None:
    result = score_news2(News2Inputs(heart_rate=150, age_years=age))
    assert (result is not None) is applicable
