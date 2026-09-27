"""Load a hospital adapter (`config/hospitals/<id>.yaml`).

The FHIR endpoint has one override: `base_url`, else the `FHIR_BASE_URL` environment variable,
else the file's `fhir_base_url`. Every FHIR call (check-in lookup, write-back, briefing document,
task completion, health check, seeding) resolves it the same way, so pointing a stack at another
server (an isolated test HAPI, say) is one setting.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

REPO: Final = Path(__file__).resolve().parents[5]
HOSPITALS: Final = REPO / "config" / "hospitals"
FHIR_BASE_URL_ENV: Final = "FHIR_BASE_URL"


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


def load_adapter(
    hospital: str = "demo_hapi", directory: Path = HOSPITALS, *, base_url: str | None = None
) -> HospitalAdapter:
    body = yaml.safe_load((directory / f"{hospital}.yaml").read_text(encoding="utf-8"))
    override = base_url or os.environ.get(FHIR_BASE_URL_ENV)
    if override:
        body["fhir_base_url"] = override
    return HospitalAdapter.model_validate(body)


__all__ = ["FHIR_BASE_URL_ENV", "HOSPITALS", "HospitalAdapter", "Timeouts", "load_adapter"]
