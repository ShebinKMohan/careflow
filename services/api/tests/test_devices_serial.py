"""Serial frames: round trip and every kind of malformed frame."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

import pytest

from careflow_api.devices import FrameError, format_frame, parse_frame

AT: Final = datetime(2026, 9, 26, 9, 3, tzinfo=UTC)


def parse(line: bytes) -> list[tuple[str, float, str]]:
    return [
        (m.kind, m.value, m.unit)
        for m in parse_frame(line, device_id="sim_vitals_01", measured_at=AT)
    ]


def test_a_full_frame_parses_with_units() -> None:
    assert parse(b"HR=112;SPO2=93;TEMP=38.6;SBP=121;DBP=79\n") == [
        ("hr", 112.0, "/min"),
        ("spo2", 93.0, "%"),
        ("temp_c", 38.6, "Cel"),
        ("sbp", 121.0, "mm[Hg]"),
        ("dbp", 79.0, "mm[Hg]"),
    ]


def test_empty_fields_are_not_measured() -> None:
    assert parse(b"HR=112;SPO2=93;TEMP=38.6;SBP=;DBP=\n") == [
        ("hr", 112.0, "/min"),
        ("spo2", 93.0, "%"),
        ("temp_c", 38.6, "Cel"),
    ]


def test_measurements_carry_the_device_and_time() -> None:
    [first, *_] = parse_frame(
        b"HR=80;SPO2=98;TEMP=36.8;SBP=;DBP=\n", device_id="dev_x", measured_at=AT
    )
    assert (first.device_id, first.measured_at) == ("dev_x", AT)


def test_format_then_parse_round_trips() -> None:
    frame = format_frame({"hr": 118.4, "spo2": 93.6, "temp_c": 39.24, "sbp": None, "dbp": None})
    assert frame == b"HR=118;SPO2=94;TEMP=39.2;SBP=;DBP=\n"
    assert [v for _, v, _ in parse(frame)] == [118.0, 94.0, 39.2]


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        (b"HR=112;SPO2=93;TEMP=38.6;SBP=;DBP=", "partial frame"),
        (b"HR=112;SPO2=9", "partial frame"),
        (b"\x00\xff\xfe garbage\n", "not ASCII"),
        (b"garbage\n", "unknown field"),
        (b"\n", "empty"),
        (b"HR=112;SPO2=93;TEMP=38.6;SBP=;DBP=;RR=20\n", "unknown field"),
        (b"HR=112;HR=113;SPO2=93;TEMP=38.6;SBP=;DBP=\n", "repeated field"),
        (b"HR=112;SPO2=93;TEMP=38.6\n", "missing fields"),
        (b"HR=abc;SPO2=93;TEMP=38.6;SBP=;DBP=\n", "not a number"),
        (b"HR=400;SPO2=93;TEMP=38.6;SBP=;DBP=\n", "out of range"),
        (b"HR=112;SPO2=101;TEMP=38.6;SBP=;DBP=\n", "out of range"),
        (b"HR=112;SPO2=93;TEMP=50.0;SBP=;DBP=\n", "out of range"),
        (b"HR=-5;SPO2=93;TEMP=38.6;SBP=;DBP=\n", "out of range"),
        (b"HR=nan;SPO2=93;TEMP=38.6;SBP=;DBP=\n", "out of range"),
        (b"HR=inf;SPO2=93;TEMP=38.6;SBP=;DBP=\n", "out of range"),
        (b"HR=112;SPO2=93;TEMP=38.6;SBP=;DBP=\r\n", "multi-line"),
        (b"HR=1" + b"0" * 200 + b"\n", "too long"),
    ],
)
def test_malformed_frames_are_rejected(line: bytes, reason: str) -> None:
    with pytest.raises(FrameError, match=reason):
        parse(line)
