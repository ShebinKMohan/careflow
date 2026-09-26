"""Turn device frames into API readings (`DeviceVitalsReading`) and hand them to a sink.

The CLI's sink POSTs to `/v1/sessions/{session_id}/vitals` like a device gateway. The dev
endpoint's sink keeps the latest reading in memory until the real vitals handler lands (M1-12).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Final

from careflow_api.devices.base import Measurement, MeasurementKind
from careflow_api.devices.simulated import SimulatedSerialDevice
from careflow_contracts.api_models import DeviceVitalsReading, LatestVitals

FIELD: Final[dict[MeasurementKind, str]] = {
    "hr": "heart_rate",
    "spo2": "spo2",
    "temp_c": "temperature_c",
    "sbp": "systolic_bp",
    "dbp": "diastolic_bp",
}

type VitalsSink = Callable[[DeviceVitalsReading], Awaitable[None]]


def to_reading(
    index: int, measurements: list[Measurement], device: SimulatedSerialDevice
) -> DeviceVitalsReading:
    if device.start is None:
        raise RuntimeError("connect() first")
    body: dict[str, object] = {
        "device_id": device.device_id,
        "reading_id": f"rd_{index + 1:04d}",
        "measured_at": device.start if not measurements else measurements[0].measured_at,
    }
    for measurement in measurements:
        body[FIELD[measurement.kind]] = measurement.value
    return DeviceVitalsReading.model_validate(body)


async def run_simulation(
    device: SimulatedSerialDevice, sink: VitalsSink, *, max_readings: int | None = None
) -> int:
    """Stream the device into the sink; returns the number of readings sent."""
    sent = 0
    await device.connect()
    try:
        async for index, measurements in device.readings():
            if max_readings is not None and sent >= max_readings:
                break
            await sink(to_reading(index, measurements, device))
            sent += 1
    finally:
        await device.close()
    return sent


class LatestVitalsStore:
    """Latest simulated reading per session (dev only; M1-12 replaces it with session facts)."""

    def __init__(self) -> None:
        self._latest: dict[str, LatestVitals] = {}

    def sink(self, session_id: str) -> VitalsSink:
        async def put(reading: DeviceVitalsReading) -> None:
            fields = reading.model_dump(exclude={"reading_id"}, exclude_none=True)
            self._latest[session_id] = LatestVitals.model_validate({**fields, "simulated": True})

        return put

    def get(self, session_id: str) -> LatestVitals | None:
        return self._latest.get(session_id)

    def clear(self) -> None:
        self._latest.clear()


latest_vitals = LatestVitalsStore()
