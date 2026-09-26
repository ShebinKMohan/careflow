"""A simulated serial vitals monitor: scenario series + seeded Gaussian noise, as ASCII frames.

Deterministic: the same scenario, seed and start time give the same frames. Frames are parsed
back with `parse_frame`, so the device path is the one real hardware would use. Blood pressure is
sent only when the scenario has a cuff and SIM_BP_CUFF=1 (the kiosk measures pulse, SpO2 and
temperature; the nurse takes blood pressure).
"""

from __future__ import annotations

import asyncio
import os
import random
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from typing import Final

from careflow_api.devices.base import Measurement, MeasurementKind
from careflow_api.devices.scenarios_loader import Scenario
from careflow_api.devices.serial_protocol import format_frame, parse_frame

#: Noise (standard deviation) per kind: a monitor's reading-to-reading jitter.
NOISE: Final[dict[MeasurementKind, float]] = {
    "hr": 1.0,
    "spo2": 0.3,
    "temp_c": 0.03,
    "sbp": 1.5,
    "dbp": 1.0,
}
DEFAULT_DEVICE_ID: Final = "sim_vitals_01"


def bp_cuff_enabled(scenario: Scenario, env: Mapping[str, str] | None = None) -> bool:
    """Both the scenario's cuff and SIM_BP_CUFF=1 are needed."""
    environ = os.environ if env is None else env
    return scenario.bp_cuff and environ.get("SIM_BP_CUFF") == "1"


class SimulatedSerialDevice:
    """Implements VitalsDevice for a scenario (one frame every `interval_s` scenario seconds)."""

    def __init__(
        self,
        scenario: Scenario,
        *,
        seed: int = 42,
        speed: float = 1.0,
        interval_s: float = 1.0,
        device_id: str = DEFAULT_DEVICE_ID,
        start: datetime | None = None,
        send_bp: bool | None = None,
    ) -> None:
        if speed < 0 or interval_s <= 0:
            raise ValueError("speed must be >= 0 and interval_s > 0")
        self.scenario = scenario
        self.seed = seed
        self.speed = speed
        self.interval_s = interval_s
        self.device_id = device_id
        self.start = start
        self.send_bp = bp_cuff_enabled(scenario) if send_bp is None else send_bp
        self._connected = False

    @property
    def frame_count(self) -> int:
        return int(self.scenario.duration_s // self.interval_s) + 1

    def frames(self) -> list[tuple[float, bytes]]:
        """(scenario time, frame) for the whole scenario; deterministic for a seed."""
        rng = random.Random(self.seed)  # noqa: S311 - simulation noise, not security
        out: list[tuple[float, bytes]] = []
        for index in range(self.frame_count):
            t = index * self.interval_s
            point = self.scenario.at(t)
            values: dict[MeasurementKind, float | None] = {
                "hr": point.hr + rng.gauss(0, NOISE["hr"]),
                "spo2": min(100.0, point.spo2 + rng.gauss(0, NOISE["spo2"])),
                "temp_c": point.temp_c + rng.gauss(0, NOISE["temp_c"]),
                "sbp": None,
                "dbp": None,
            }
            sbp_noise, dbp_noise = rng.gauss(0, NOISE["sbp"]), rng.gauss(0, NOISE["dbp"])
            if self.send_bp and point.sbp is not None and point.dbp is not None:
                values["sbp"], values["dbp"] = point.sbp + sbp_noise, point.dbp + dbp_noise
            out.append((t, format_frame(values)))
        return out

    async def connect(self) -> None:
        if self.start is None:
            self.start = datetime.now(UTC)
        self._connected = True

    async def close(self) -> None:
        self._connected = False

    async def readings(self) -> AsyncIterator[tuple[int, list[Measurement]]]:
        """One parsed frame at a time, paced by `speed` (0 = as fast as possible)."""
        if not self._connected or self.start is None:
            raise RuntimeError("connect() first")
        previous = 0.0
        for index, (t, frame) in enumerate(self.frames()):
            if self.speed > 0 and t > previous:
                await asyncio.sleep((t - previous) / self.speed)
            previous = t
            measured_at = self.start + timedelta(seconds=t)
            yield index, parse_frame(frame, device_id=self.device_id, measured_at=measured_at)

    async def stream(self) -> AsyncIterator[Measurement]:
        async for _, measurements in self.readings():
            for measurement in measurements:
                yield measurement
