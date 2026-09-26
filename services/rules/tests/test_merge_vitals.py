"""A nurse value always wins over a device value; otherwise the newer reading wins."""

from __future__ import annotations

from careflow_contracts import Vitals
from careflow_rules import merge_vitals
from careflow_rules.testing import vital


def vitals(**facts: object) -> Vitals:
    return Vitals.model_validate(facts)


def test_nurse_value_wins_even_when_the_device_reading_is_newer() -> None:
    nurse = vital("heart_rate", 118, "NURSE_CONFIRMED", "2026-09-26T09:06:00Z")
    device = vital("heart_rate", 125, "DEVICE_MEASURED", "2026-09-26T09:10:00Z")
    merged = merge_vitals(vitals(heart_rate=nurse), vitals(heart_rate=device))
    assert merged.heart_rate is not None and merged.heart_rate.value == 118
    merged = merge_vitals(vitals(heart_rate=device), vitals(heart_rate=nurse))
    assert merged.heart_rate is not None and merged.heart_rate.value == 118


def test_same_source_keeps_the_newer_reading_and_other_paths_carry_over() -> None:
    old = vital("spo2", 93, "DEVICE_MEASURED", "2026-09-26T09:03:00Z")
    new = vital("spo2", 95, "DEVICE_MEASURED", "2026-09-26T09:08:00Z")
    temp = vital("temperature_c", 39.4, "DEVICE_MEASURED")
    merged = merge_vitals(vitals(spo2=new, temperature_c=temp), vitals(spo2=old))
    assert merged.spo2 is not None and merged.spo2.value == 95
    assert merged.temperature_c is not None and merged.temperature_c.value == 39.4
    assert merge_vitals(None, vitals(spo2=old)).spo2 is not None
