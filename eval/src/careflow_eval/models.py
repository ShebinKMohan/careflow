"""Strict, serialisable synthetic scenario contracts shared by the eval commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt

from careflow_contracts.generated.alert_schema import RuleId
from careflow_contracts.generated.structured_facts_schema import FactKey, Language

type Scalar = str | StrictBool | StrictInt | StrictFloat
type Category = Literal["chest_pain", "stroke", "sepsis", "child", "breathing", "routine"]


class ScenarioModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Patient(ScenarioModel):
    age_years: Annotated[StrictInt, Field(ge=0, le=120)]
    sex: Literal["male", "female", "other", "unknown"]


class ScenarioTurn(ScenarioModel):
    question_key: FactKey
    patient_text: Annotated[str, Field(min_length=1, max_length=1000)]


class Expected(ScenarioModel):
    facts: dict[FactKey, Scalar | list[Scalar]]
    alerts: list[RuleId]
    level: Annotated[StrictInt, Field(ge=1, le=5)]
    level_rationale: Annotated[str, Field(min_length=1, max_length=2000)]


class GeneratedBy(ScenarioModel):
    tool: Annotated[str, Field(min_length=1, max_length=100)]
    model: Annotated[str, Field(min_length=1, max_length=200)]
    date: date


class Scenario(ScenarioModel):
    synthetic: Literal[True]
    id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=48)]
    category: Category
    language: Literal[Language.en] = Language.en
    description: Annotated[str, Field(min_length=1, max_length=2000)]
    patient: Patient
    turns: Annotated[list[ScenarioTurn], Field(min_length=1, max_length=100)]
    device_vitals: str | dict[str, Scalar] = Field(default_factory=dict)
    nurse_vitals: dict[str, Scalar] = Field(default_factory=dict)
    expected: Expected
    generated_by: GeneratedBy
    reviewed_by: Annotated[str | None, Field(pattern=r"^agent:[^/\s]+/\S+$")] = None
    reviewed_at: AwareDatetime | None = None
    review_method: Literal["automated"] | None = None
    reviewed_content_sha256: Annotated[str | None, Field(pattern=r"^[0-9a-f]{64}$")] = None


@dataclass(frozen=True)
class Dataset:
    scenarios: tuple[Scenario, ...]
    sha256: str
