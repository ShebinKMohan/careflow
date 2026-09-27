"""`check_briefing`: every item's source must be valid, or the item is marked "source missing".

Fails closed (safety rule 4): an item whose source cannot be verified is still shown, marked
`source_missing` (red on screen and in the document), and logged as an error. The tests require
none. A source is valid when:

- it is the kind its provenance pairs with (EHR-fhir, PATIENT_STATED-transcript_span,
  DEVICE_MEASURED-device, NURSE_CONFIRMED-nurse, MODEL_INFERRED-model);
- an item that names a fact matches that fact in the session exactly (tag and source);
- any other item cites a source the session holds (a fact's, the deciding nurse's, or the
  suggesting model's);
- a transcript span points into a patient turn of this session and its quote is that turn's text
  between the offsets.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Final

from careflow_contracts.api_models import Briefing, BriefingItem
from careflow_contracts.generated.fact_schema import (
    DeviceSource,
    FactBase,
    FhirSource,
    ModelSource,
    NurseSource,
    Provenance,
    TranscriptSpanSource,
)
from careflow_contracts.generated.structured_facts_schema import Turn, TurnRole
from careflow_contracts.validators import iter_facts
from careflow_intake.state import IntakeState, structured_facts

logger = logging.getLogger(__name__)

PAIRS: Final[dict[Provenance, type]] = {
    Provenance.EHR: FhirSource,
    Provenance.PATIENT_STATED: TranscriptSpanSource,
    Provenance.DEVICE_MEASURED: DeviceSource,
    Provenance.NURSE_CONFIRMED: NurseSource,
    Provenance.MODEL_INFERRED: ModelSource,
}


def source_key(provenance: Provenance, source: object) -> str:
    dump = getattr(source, "model_dump", None)
    body = dump(mode="json", exclude_none=True) if dump is not None else source
    return json.dumps([provenance.value, body], sort_keys=True)


@dataclass(frozen=True)
class SourceIndex:
    """Every source the session holds."""

    facts: dict[str, FactBase]
    sources: frozenset[str]
    turns: dict[str, Turn]

    @classmethod
    def of(cls, state: IntakeState) -> SourceIndex:
        facts = {fact.fact_id: fact for _, fact in iter_facts(structured_facts(state))}
        sources = {source_key(fact.provenance, fact.source) for fact in facts.values()}
        if (decision := state.get("nurse_decision")) is not None:
            nurse = NurseSource(kind="nurse", staff_user_id=decision.nurse_user_id)
            sources.add(source_key(Provenance.NURSE_CONFIRMED, nurse))
        if (suggestion := state.get("suggestion")) is not None:
            model = ModelSource(
                kind="model", model_id=suggestion.model_id, model_version=suggestion.model_version
            )
            sources.add(source_key(Provenance.MODEL_INFERRED, model))
        turns = {turn.turn_id: turn for turn in state.get("turns", [])}
        return cls(facts, frozenset(sources), turns)


def item_problem(item: BriefingItem, index: SourceIndex) -> str | None:
    """Why the item's source is not valid, or None."""
    source = item.source
    expected = PAIRS[item.provenance]
    if not isinstance(source, expected):
        return f"a {item.provenance.value} item needs a {expected.__name__}"
    if item.fact_id is not None:
        fact = index.facts.get(item.fact_id)
        if fact is None:
            return f"fact {item.fact_id} is not in the session"
        if fact.provenance is not item.provenance:
            return f"the tag differs from fact {item.fact_id}'s provenance"
        if source_key(fact.provenance, fact.source) != source_key(item.provenance, source):
            return f"the source differs from fact {item.fact_id}'s"
    elif source_key(item.provenance, source) not in index.sources:
        return "the source is not one of the session's"
    if isinstance(source, TranscriptSpanSource):
        turn = index.turns.get(source.turn_id)
        if turn is None or turn.role is not TurnRole.patient:
            return f"turn {source.turn_id} is not a patient turn of the session"
        if turn.text[source.start : source.end] != source.quote:
            return f"the quote does not match turn {source.turn_id}"
    return None


def check_briefing(briefing: Briefing, state: IntakeState) -> tuple[Briefing, list[str]]:
    """The briefing with every unverifiable item marked `source_missing`, and the problems."""
    index = SourceIndex.of(state)
    problems: list[str] = []
    sections = []
    for section in briefing.sections:
        items = []
        for position, item in enumerate(section.items):
            problem = item_problem(item, index)
            if problem is None and item.source_missing:
                problem = "the builder found no source"
            if problem is not None:
                logger.error(
                    "briefing.source_missing",
                    extra={
                        "session_id": briefing.session_id,
                        "section": section.id.value,
                        "item": position,
                        "reason": problem,
                    },
                )
                problems.append(f"{section.id.value}[{position}]: {problem}")
                item = item.model_copy(update={"source_missing": True})
            items.append(item)
        sections.append(section.model_copy(update={"items": items}))
    return briefing.model_copy(update={"sections": sections}), problems


__all__ = ["PAIRS", "SourceIndex", "check_briefing", "item_problem", "source_key"]
