"""Strict, serialisable synthetic scenario contracts shared by the eval commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    model_validator,
)

from careflow_contracts.generated.alert_schema import RuleId
from careflow_contracts.generated.structured_facts_schema import FactKey, Language

type Scalar = str | StrictBool | StrictInt | StrictFloat
type Category = Literal["chest_pain", "stroke", "sepsis", "child", "breathing", "routine"]
type Dialect = Literal["gulf", "levantine"]


class ScenarioModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Patient(ScenarioModel):
    age_years: Annotated[StrictInt, Field(ge=0, le=120)]
    sex: Literal["male", "female", "other", "unknown"]


class ScenarioTurn(ScenarioModel):
    question_key: FactKey
    patient_text: Annotated[str, Field(min_length=1, max_length=1000)]
    # Arabic scenarios (M2-03): the writer's English gloss of the answer, the reference its
    # blind back-translation is checked against, and the gold keys the answer states. Neither
    # is ever shown to the extractor.
    gloss_en: Annotated[str, Field(min_length=1, max_length=1000)] | None = None
    states: list[FactKey] = Field(default_factory=list)


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
    language: Language = Language.en
    dialect: Dialect | None = None
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

    @model_validator(mode="after")
    def _language_fields(self) -> Scenario:
        """An Arabic scenario names its dialect, and every answer has a gloss and says which
        gold keys it states; an English one has none of these (its content hash is unchanged)."""
        if self.language is Language.ar:
            if self.dialect is None:
                raise ValueError("an Arabic scenario needs a dialect")
            if any(turn.gloss_en is None for turn in self.turns):
                raise ValueError("every Arabic answer needs gloss_en")
        elif self.dialect is not None or any(t.gloss_en or t.states for t in self.turns):
            raise ValueError("dialect, gloss_en and states are for Arabic scenarios only")
        return self


@dataclass(frozen=True)
class Dataset:
    scenarios: tuple[Scenario, ...]
    sha256: str
