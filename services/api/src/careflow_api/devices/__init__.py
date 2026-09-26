"""Simulated vitals device (M1-03). There is no real hardware: every reading is labelled simulated.

`SimulatedSerialDevice` plays a scenario (scenarios/*.yaml) as the ASCII frames a bedside monitor
would send over serial, and parses them back with `parse_frame`, so the parsing path always runs.
"""

from careflow_api.devices.base import LOINC, Measurement, MeasurementKind, VitalsDevice
from careflow_api.devices.scenarios_loader import (
    Scenario,
    VitalsPoint,
    load_scenario,
    scenario_names,
)
from careflow_api.devices.serial_protocol import FrameError, format_frame, parse_frame
from careflow_api.devices.simulated import SimulatedSerialDevice, bp_cuff_enabled

__all__ = [
    "LOINC",
    "FrameError",
    "Measurement",
    "MeasurementKind",
    "Scenario",
    "SimulatedSerialDevice",
    "VitalsDevice",
    "VitalsPoint",
    "bp_cuff_enabled",
    "format_frame",
    "load_scenario",
    "parse_frame",
    "scenario_names",
]
