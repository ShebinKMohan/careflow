"""Simulated device: scenarios, interpolation, determinism, the BP flags and the parsing path."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest
import yaml

from careflow_api.devices import (
    LOINC,
    SimulatedSerialDevice,
    bp_cuff_enabled,
    load_scenario,
    parse_frame,
    scenario_names,
)
from careflow_api.devices.gateway import LatestVitalsStore, run_simulation, to_reading

START: Final = datetime(2026, 9, 26, 9, 3, tzinfo=UTC)
REPO: Final = Path(__file__).resolve().parents[3]
MAPPING: Final = REPO / "packages/contracts/fhir/mapping.yaml"
E2E_FIXTURE: Final = REPO / "apps/kiosk/tests/e2e/fixtures/vitals-hypoxic.json"


def device(name: str, **kwargs: object) -> SimulatedSerialDevice:
    """No blood pressure unless a test asks (send_bp=None reads the cuff and SIM_BP_CUFF)."""
    kwargs.setdefault("send_bp", False)
    return SimulatedSerialDevice(load_scenario(name), speed=0, start=START, **kwargs)  # type: ignore[arg-type]


def values(name: str, **kwargs: object) -> list[dict[str, float]]:
    out = []
    for _, frame in device(name, **kwargs).frames():
        out.append({m.kind: m.value for m in parse_frame(frame, device_id="d", measured_at=START)})
    return out


def test_the_four_scenarios_exist() -> None:
    assert scenario_names() == ["hypoxic", "normal", "septic", "tachycardic_child"]
    assert load_scenario("tachycardic_child").age_band == "child"


def test_interpolation_is_linear() -> None:
    mid = load_scenario("septic").at(15)
    assert (mid.hr, mid.spo2, round(mid.temp_c, 2)) == (122.0, 93.5, 39.35)
    assert load_scenario("septic").at(-5).hr == 118.0 and load_scenario("septic").at(99).hr == 126.0


def test_same_seed_same_readings_other_seed_other_readings() -> None:
    assert device("septic").frames() == device("septic").frames()
    assert device("septic", seed=7).frames() != device("septic").frames()


@pytest.mark.parametrize(
    ("scenario", "env", "expected"),
    [
        ("normal", "1", True),
        ("normal", "0", False),
        ("normal", None, False),
        ("tachycardic_child", "1", False),
    ],
)
def test_blood_pressure_needs_the_cuff_and_the_env_flag(
    monkeypatch: pytest.MonkeyPatch, scenario: str, env: str | None, expected: bool
) -> None:
    if env is None:
        monkeypatch.delenv("SIM_BP_CUFF", raising=False)
    else:
        monkeypatch.setenv("SIM_BP_CUFF", env)
    assert bp_cuff_enabled(load_scenario(scenario)) is expected
    first = values(scenario, send_bp=None)[0]
    assert ("sbp" in first and "dbp" in first) is expected


def test_scenario_targets() -> None:
    normal, septic, hypoxic, child = (
        values(n) for n in ("normal", "septic", "hypoxic", "tachycardic_child")
    )
    assert all(
        abs(v["hr"] - 78) <= 3 and abs(v["spo2"] - 98) <= 1 and abs(v["temp_c"] - 36.8) <= 0.2
        for v in normal
    )
    assert abs(septic[0]["hr"] - 118) <= 3 and abs(septic[-1]["hr"] - 126) <= 3
    assert abs(septic[0]["temp_c"] - 39.2) <= 0.2 and abs(septic[-1]["temp_c"] - 39.5) <= 0.2
    assert min(v["spo2"] for v in hypoxic[:5]) <= 90 and hypoxic[-1]["spo2"] <= 89
    assert all(abs(v["hr"] - 165) <= 3 and "sbp" not in v for v in child)


def test_septic_readings_with_a_stated_fever_give_a_partial_sepsis_alert() -> None:
    """The scenario's purpose: kiosk vitals + fever reach partial NEWS2 >= 5 (M1-01 rules)."""
    from careflow_rules import evaluate
    from careflow_rules.testing import build

    first = values("septic")[0]
    facts = build(
        vitals={"heart_rate": first["hr"], "spo2": first["spo2"], "temperature_c": first["temp_c"]},
        infection={"fever_reported": "yes"},
        age=60,
    )
    result = evaluate(facts, START)
    assert result.news2 is not None and result.news2.total >= 5 and result.news2.partial
    assert [a.rule_id.value for a in result.alerts] == ["SEPSIS_NEWS2"]


def test_stream_yields_parsed_measurements_one_second_apart() -> None:
    async def collect() -> list[tuple[str, datetime]]:
        d = device("hypoxic")
        await d.connect()
        return [(m.kind, m.measured_at) async for m in d.stream()]

    measurements = asyncio.run(collect())
    assert len(measurements) == 31 * 3  # HR, SpO2 and temperature; no BP by default
    assert measurements[0] == ("hr", START) and measurements[-1][1] == START + timedelta(seconds=30)


def test_speed_paces_the_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr("careflow_api.devices.simulated.asyncio.sleep", fake_sleep)

    async def run() -> None:
        d = SimulatedSerialDevice(load_scenario("normal"), speed=10, start=START)
        await d.connect()
        async for _ in d.readings():
            pass

    asyncio.run(run())
    assert waits == [pytest.approx(0.1)] * 30


def test_readings_become_api_bodies_and_reach_the_sink() -> None:
    store = LatestVitalsStore()
    sent = asyncio.run(run_simulation(device("hypoxic"), store.sink("ses_1"), max_readings=5))
    latest = store.get("ses_1")
    assert sent == 5 and latest is not None
    assert latest.simulated is True and latest.device_id == "sim_vitals_01"
    assert latest.measured_at == START + timedelta(seconds=4)
    assert latest.spo2 is not None and latest.spo2 <= 90
    reading = to_reading(
        0,
        parse_frame(
            b"HR=80;SPO2=98;TEMP=36.8;SBP=;DBP=\n", device_id="sim_vitals_01", measured_at=START
        ),
        device("normal"),
    )
    assert reading.model_dump(mode="json", exclude_none=True) == {
        "device_id": "sim_vitals_01",
        "reading_id": "rd_0001",
        "measured_at": "2026-09-26T09:03:00Z",
        "heart_rate": 80.0,
        "spo2": 98.0,
        "temperature_c": 36.8,
    }


def test_loinc_codes_match_the_fhir_mapping() -> None:
    mapping = yaml.safe_load(MAPPING.read_text(encoding="utf-8"))
    codes = {e["fact_path"]: str(e["code"]) for e in mapping["entries"]}
    assert LOINC == {
        "hr": codes["vitals.heart_rate"],
        "spo2": codes["vitals.spo2"],
        "temp_c": codes["vitals.temperature_c"],
        "sbp": codes["vitals.systolic_bp"],
        "dbp": codes["vitals.diastolic_bp"],
    }


def test_kiosk_e2e_fixture_is_the_simulator_output() -> None:
    """apps/kiosk/tests/e2e/fixtures/vitals-hypoxic.json replays this simulator (seed 42)."""
    frames: list[dict[str, object]] = []

    async def sink(reading: object) -> None:
        from careflow_contracts.api_models import DeviceVitalsReading, LatestVitals

        assert isinstance(reading, DeviceVitalsReading)
        fields = reading.model_dump(mode="json", exclude={"reading_id"}, exclude_none=True)
        latest = LatestVitals.model_validate({**fields, "simulated": True})
        frames.append(latest.model_dump(mode="json", exclude_none=True))

    asyncio.run(run_simulation(device("hypoxic"), sink))
    assert json.loads(E2E_FIXTURE.read_text(encoding="utf-8")) == frames
