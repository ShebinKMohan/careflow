"""The device interface and one measurement."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal, Protocol

type MeasurementKind = Literal["hr", "spo2", "temp_c", "sbp", "dbp"]

#: UCUM unit per kind (the contract's vitals units).
UNITS: Final[dict[MeasurementKind, str]] = {
    "hr": "/min",
    "spo2": "%",
    "temp_c": "Cel",
    "sbp": "mm[Hg]",
    "dbp": "mm[Hg]",
}
#: LOINC code per kind, as in packages/contracts/fhir/mapping.yaml (SpO2 also carries 59408-5).
LOINC: Final[dict[MeasurementKind, str]] = {
    "hr": "8867-4",
    "spo2": "2708-6",
    "temp_c": "8310-5",
    "sbp": "8480-6",
    "dbp": "8462-4",
}


@dataclass(frozen=True, slots=True)
class Measurement:
    kind: MeasurementKind
    value: float
    unit: str
    measured_at: datetime
    device_id: str


class VitalsDevice(Protocol):
    """A vitals monitor: connect, stream measurements, close."""

    async def connect(self) -> None: ...

    def stream(self) -> AsyncIterator[Measurement]: ...

    async def close(self) -> None: ...
