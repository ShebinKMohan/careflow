"""Load a hospital adapter (`config/hospitals/<id>.yaml`)."""

from __future__ import annotations

from pathlib import Path
from typing import Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

REPO: Final = Path(__file__).resolve().parents[5]
HOSPITALS: Final = REPO / "config" / "hospitals"


class Timeouts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    connect_s: float = 2.0
    read_s: float = 5.0
    total_s: float = 8.0


class HospitalAdapter(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    fhir_base_url: str
    identifier_systems: dict[str, str]
    display_paths: list[str] = Field(default_factory=lambda: ["text", "coding[0].display"])
    condition_categories: list[str] = Field(default_factory=list)
    medication_types: list[str] = Field(
        default_factory=lambda: ["MedicationStatement", "MedicationRequest"]
    )
    appointment_statuses: list[str] = Field(default_factory=lambda: ["booked", "arrived"])
    timeouts: Timeouts = Field(default_factory=Timeouts)
    #: Encounter.class (v3 ActCode) of the triage encounter CareFlow writes back (M1-09).
    encounter_class: Literal["AMB", "EMER"] = "AMB"


def load_adapter(hospital: str = "demo_hapi", directory: Path = HOSPITALS) -> HospitalAdapter:
    body = yaml.safe_load((directory / f"{hospital}.yaml").read_text(encoding="utf-8"))
    return HospitalAdapter.model_validate(body)


__all__ = ["HOSPITALS", "HospitalAdapter", "Timeouts", "load_adapter"]
