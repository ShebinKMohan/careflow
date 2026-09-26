"""Scenario files (scenarios/*.yaml): a vitals time series with linear interpolation."""

from __future__ import annotations

from functools import cache
from importlib.resources import files
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class VitalsPoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    t_s: float = Field(ge=0)
    hr: float
    spo2: float
    temp_c: float
    sbp: float | None = None
    dbp: float | None = None


class Scenario(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    description: str
    age_band: Literal["adult", "child"]
    duration_s: int = Field(gt=0)
    bp_cuff: bool
    series: list[VitalsPoint] = Field(min_length=2)

    @model_validator(mode="after")
    def _ordered_series(self) -> Scenario:
        times = [p.t_s for p in self.series]
        if times != sorted(times) or len(set(times)) != len(times):
            raise ValueError("series times must strictly increase")
        if times[0] != 0 or times[-1] != self.duration_s:
            raise ValueError("series must start at 0 and end at duration_s")
        return self

    def at(self, t_s: float) -> VitalsPoint:
        """Linear interpolation between the surrounding points (clamped to the series)."""
        t = min(max(t_s, 0.0), float(self.duration_s))
        for before, after in zip(self.series, self.series[1:], strict=False):
            if before.t_s <= t <= after.t_s:
                share = (t - before.t_s) / (after.t_s - before.t_s)
                return VitalsPoint(
                    t_s=t,
                    hr=_lerp(before.hr, after.hr, share),
                    spo2=_lerp(before.spo2, after.spo2, share),
                    temp_c=_lerp(before.temp_c, after.temp_c, share),
                    sbp=_lerp_optional(before.sbp, after.sbp, share),
                    dbp=_lerp_optional(before.dbp, after.dbp, share),
                )
        return self.series[-1]  # pragma: no cover - the loop covers [0, duration]


def _lerp(a: float, b: float, share: float) -> float:
    return a + (b - a) * share


def _lerp_optional(a: float | None, b: float | None, share: float) -> float | None:
    return None if a is None or b is None else _lerp(a, b, share)


def scenario_names() -> list[str]:
    folder = files("careflow_api.devices") / "scenarios"
    return sorted(
        p.name.removesuffix(".yaml") for p in folder.iterdir() if p.name.endswith(".yaml")
    )


@cache
def load_scenario(name: str) -> Scenario:
    if name not in scenario_names():
        raise KeyError(f"unknown vitals scenario: {name}")
    text = (files("careflow_api.devices") / "scenarios" / f"{name}.yaml").read_text(
        encoding="utf-8"
    )
    return Scenario.model_validate(yaml.safe_load(text))
