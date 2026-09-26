"""The ASCII frame format of the simulated monitor, e.g. `HR=112;SPO2=93;TEMP=38.6;SBP=;DBP=\n`.

One frame per line; fields are KEY=VALUE separated by `;`; an empty value means "not measured".
`parse_frame` rejects anything else: a missing newline (partial line), non-ASCII bytes, unknown
or repeated keys, non-numeric values and values outside the plausible range.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Final

from careflow_api.devices.base import UNITS, Measurement, MeasurementKind

FIELDS: Final[dict[str, MeasurementKind]] = {
    "HR": "hr",
    "SPO2": "spo2",
    "TEMP": "temp_c",
    "SBP": "sbp",
    "DBP": "dbp",
}
ORDER: Final = ("HR", "SPO2", "TEMP", "SBP", "DBP")
#: Plausible range per kind; a monitor reporting outside it is faulty.
RANGES: Final[dict[MeasurementKind, tuple[float, float]]] = {
    "hr": (20, 300),
    "spo2": (50, 100),
    "temp_c": (25, 45),
    "sbp": (40, 300),
    "dbp": (20, 200),
}
MAX_FRAME_BYTES: Final = 128


class FrameError(ValueError):
    """A malformed or implausible frame."""


def format_frame(values: dict[MeasurementKind, float | None]) -> bytes:
    """Encode one reading. HR and SpO2 are whole numbers; temperature has one decimal."""
    parts: list[str] = []
    for key in ORDER:
        kind = FIELDS[key]
        value = values.get(kind)
        if value is None:
            parts.append(f"{key}=")
        elif kind == "temp_c":
            parts.append(f"{key}={value:.1f}")
        else:
            parts.append(f"{key}={round(value)}")
    return (";".join(parts) + "\n").encode("ascii")


def parse_frame(line: bytes, *, device_id: str, measured_at: datetime) -> list[Measurement]:
    """Decode one frame into measurements (empty fields are skipped)."""
    if len(line) > MAX_FRAME_BYTES:
        raise FrameError("frame too long")
    if not line.endswith(b"\n"):
        raise FrameError("partial frame: no newline")
    try:
        text = line[:-1].decode("ascii")
    except UnicodeDecodeError as exc:
        raise FrameError("not ASCII") from exc
    if not text or "\n" in text or "\r" in text:
        raise FrameError("empty or multi-line frame")
    measurements: list[Measurement] = []
    seen: set[str] = set()
    for field in text.split(";"):
        key, sep, raw = field.partition("=")
        if not sep or key not in FIELDS:
            raise FrameError(f"unknown field: {field!r}")
        if key in seen:
            raise FrameError(f"repeated field: {key}")
        seen.add(key)
        if raw == "":
            continue
        kind = FIELDS[key]
        try:
            value = float(raw)
        except ValueError as exc:
            raise FrameError(f"not a number: {key}={raw!r}") from exc
        low, high = RANGES[kind]
        if not math.isfinite(value) or not low <= value <= high:
            raise FrameError(f"out of range: {key}={raw}")
        measurements.append(
            Measurement(kind, value, UNITS[kind], measured_at=measured_at, device_id=device_id)
        )
    if seen != set(FIELDS):
        raise FrameError(f"missing fields: {sorted(set(FIELDS) - seen)}")
    return measurements
