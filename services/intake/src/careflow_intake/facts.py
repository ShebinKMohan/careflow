"""Facts in the intake state: one contract fact plus its StructuredFacts path (`FactKey`).

The graph keeps a flat list of `FactEntry`, merged by fact id, so facts from the conversation,
devices and nurses can arrive in any order (`aupdate_state(config, {"facts": [...]})`).
`to_structured_facts` builds the contract `StructuredFacts` that the rules engine and the
urgency model read.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from functools import cache
from typing import Any, Final, get_args

from pydantic import BaseModel, ConfigDict, SerializeAsAny, model_validator

from careflow_contracts.generated.fact_schema import FactBase, Provenance
from careflow_contracts.generated.structured_facts_schema import (
    EhrContext,
    ExtractionStatus,
    FactKey,
    Language,
    StructuredFacts,
    Symptom,
)

# Keys whose StructuredFacts field is a list: every entry is kept, not only the current one.
LIST_KEYS: Final = frozenset(
    {
        FactKey.symptoms,
        FactKey.ehr_context_allergies,
        FactKey.ehr_context_conditions,
        FactKey.ehr_context_medications,
    }
)


def _models_in(annotation: Any) -> list[type[BaseModel]]:
    args = get_args(annotation)
    if not args:
        return (
            [annotation]
            if isinstance(annotation, type) and issubclass(annotation, BaseModel)
            else []
        )
    return [model for arg in args for model in _models_in(arg)]


@cache
def fact_class(key: FactKey) -> type[BaseModel]:
    """The contract model stored at `key`: the leaf fact class, the list item class for list
    keys (`CodeFact` for `ehr_context.allergies`), or `Symptom` for `symptoms`."""
    model: type[BaseModel] = StructuredFacts
    for part in key.value.split("."):
        found = _models_in(model.model_fields[part].annotation)
        if not found:
            raise KeyError(f"not a fact path: {key.value}")
        model = found[0]
    return model


class FactEntry(BaseModel):
    """One fact in the intake state. `fact` is validated as the contract class of `key`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: FactKey
    fact: SerializeAsAny[BaseModel]

    @model_validator(mode="before")
    @classmethod
    def _typed_fact(cls, data: Any) -> Any:
        if isinstance(data, dict) and "key" in data and "fact" in data:
            expected = fact_class(FactKey(data["key"]))
            fact = data["fact"]
            if not isinstance(fact, expected):
                raw = fact.model_dump(mode="json") if isinstance(fact, BaseModel) else fact
                fact = expected.model_validate(raw)
            data = {**data, "fact": fact}
        return data

    @property
    def base(self) -> FactBase:
        """The fact that carries the id, provenance and time (a symptom's `name`)."""
        fact = self.fact
        if isinstance(fact, Symptom):
            return fact.name
        if isinstance(fact, FactBase):
            return fact
        raise TypeError(f"{self.key.value}: not a contract fact")

    @property
    def fact_id(self) -> str:
        return self.base.fact_id

    @property
    def provenance(self) -> Provenance:
        return self.base.provenance

    @property
    def recorded_at(self) -> datetime:
        return self.base.recorded_at


def as_entry(value: FactEntry | dict[str, Any]) -> FactEntry:
    return value if isinstance(value, FactEntry) else FactEntry.model_validate(value)


def merge_facts_by_id(
    existing: Sequence[FactEntry] | None,
    new: Sequence[FactEntry | dict[str, Any]] | FactEntry | None,
) -> list[FactEntry]:
    """LangGraph reducer: a fact with a known id replaces the old one in place; new ids append."""
    merged = {entry.fact_id: entry for entry in existing or []}
    incoming = [new] if isinstance(new, FactEntry) else list(new or [])
    for value in incoming:
        entry = as_entry(value)
        merged[entry.fact_id] = entry
    return list(merged.values())


def _rank(entry: FactEntry, position: int) -> tuple[bool, datetime, int]:
    return (entry.provenance is Provenance.NURSE_CONFIRMED, entry.recorded_at, position)


def current_entries(entries: Iterable[FactEntry]) -> list[FactEntry]:
    """Every list-key entry, and one entry per single-value key: a nurse-confirmed fact first,
    then the most recently recorded, then the later one in the list."""
    listed: list[FactEntry] = []
    best: dict[FactKey, tuple[tuple[bool, datetime, int], FactEntry]] = {}
    for position, entry in enumerate(entries):
        if entry.key in LIST_KEYS:
            listed.append(entry)
            continue
        rank = _rank(entry, position)
        if entry.key not in best or rank > best[entry.key][0]:
            best[entry.key] = (rank, entry)
    return listed + [entry for _, entry in best.values()]


def to_structured_facts(
    *,
    session_id: str,
    clinic_id: str,
    language: Language,
    entries: Iterable[FactEntry],
    ehr_context: EhrContext | None = None,
    extraction_status: ExtractionStatus = ExtractionStatus.complete,
) -> StructuredFacts:
    """The contract object for the rules engine and the urgency model (validated)."""
    body: dict[str, Any] = {
        "session_id": session_id,
        "clinic_id": clinic_id,
        "language": language,
        "extraction_status": extraction_status,
    }
    if ehr_context is not None:
        body["ehr_context"] = ehr_context.model_dump(mode="json", exclude_none=True)
    for entry in current_entries(entries):
        *groups, name = entry.key.value.split(".")
        container = body
        for group in groups:
            container = container.setdefault(group, {})
        value = entry.fact.model_dump(mode="json", exclude_none=True)
        if entry.key in LIST_KEYS:
            container.setdefault(name, []).append(value)
        else:
            container[name] = value
    return StructuredFacts.model_validate(body)


__all__ = [
    "LIST_KEYS",
    "FactEntry",
    "as_entry",
    "current_entries",
    "fact_class",
    "merge_facts_by_id",
    "to_structured_facts",
]
