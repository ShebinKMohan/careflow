"""The versioned extraction prompts and the messages sent to the model. The session language
picks the prompt (M2-03): the Arabic one keeps the English keys and values and quotes Arabic."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import cache
from pathlib import Path
from typing import Final

from careflow_contracts.generated.fact_schema import (
    IntegerFact,
    TextFact,
    TranscriptSpanSource,
    TriStateFact,
)
from careflow_contracts.generated.structured_facts_schema import (
    FactKey,
    Language,
    Symptom,
    Turn,
    TurnRole,
)
from careflow_intake.facts import FactEntry, fact_class
from careflow_providers.base import Message

PROMPT_VERSIONS: Final = {Language.en: "extraction_v2", Language.ar: "extraction_v2_ar"}
PROMPT_VERSION: Final = PROMPT_VERSIONS[Language.en]
PROMPT_DIR: Final = Path(__file__).parent / "prompts"


def prompt_version(language: Language | str = Language.en) -> str:
    return PROMPT_VERSIONS[Language(language)]


@cache
def template(version: str = PROMPT_VERSION) -> str:
    return (PROMPT_DIR / f"{version}.md").read_text(encoding="utf-8")


@cache
def extractable_keys() -> tuple[FactKey, ...]:
    """Keys a patient can state: yes/no answers, whole numbers (age, pain score), free text and
    symptoms. Coded, EHR, model-inferred, vital-sign and nurse-task keys come from other sources
    (devices, nurses, the EHR), never from the patient's words."""
    return tuple(
        key
        for key in FactKey
        if not key.value.startswith(("vitals.", "tasks."))
        and issubclass(fact_class(key), TriStateFact | TextFact | IntegerFact | Symptom)
    )


def turn_labels(turns: Sequence[Turn]) -> dict[str, Turn]:
    """`t1..tn` in transcript order: short labels the model copies more reliably than ids."""
    return {f"t{index}": turn for index, turn in enumerate(turns, start=1)}


def registration_spans(
    turns: Sequence[Turn], existing_facts: Sequence[FactEntry]
) -> dict[str, tuple[int, int] | None]:
    """Presentation spans for the reserved server registration turn; None hides metadata.

    Both already-sourced age and sex must identify the reserved turn. Ordinary patient text
    is never classified by its wording. Only an existing chief-complaint source may expose a
    natural registration reason; the underlying transcript remains untouched for verification.
    """
    turn = next((turn for turn in turns if turn.turn_id == "trn_reception_001"), None)
    if turn is None or turn.role is not TurnRole.patient:
        return {}
    sourced = [
        entry
        for entry in existing_facts
        if isinstance(entry.base.source, TranscriptSpanSource)
        and entry.base.source.turn_id == turn.turn_id
        and entry.base.source.quote == turn.text[entry.base.source.start : entry.base.source.end]
    ]
    if not {FactKey.patient_age_years, FactKey.patient_sex} <= {entry.key for entry in sourced}:
        return {}
    for entry in sourced:
        source = entry.base.source
        if (
            entry.key is FactKey.chief_complaint
            and isinstance(source, TranscriptSpanSource)
            and getattr(entry.fact, "value", None) == source.quote
        ):
            return {turn.turn_id: (source.start, source.end)}
    return {turn.turn_id: None}


def build_messages(
    turns: Sequence[Turn],
    question_keys: Mapping[str, FactKey],
    language: Language | str = Language.en,
    *,
    existing_facts: Sequence[FactEntry] = (),
) -> list[Message]:
    """A system message with the rules and allowed keys (in the session language's prompt), and
    the transcript with original labels, role and question key. Typed registration metadata
    is omitted from display; a sourced registration reason remains visible."""
    keys = ", ".join(key.value for key in extractable_keys())
    system = template(prompt_version(language)).replace("{keys}", keys)
    lines = []
    registered = registration_spans(turns, existing_facts)
    for label, turn in turn_labels(turns).items():
        text = turn.text
        if turn.turn_id in registered:
            span = registered[turn.turn_id]
            if span is None:
                continue
            text = turn.text[span[0] : span[1]]
        if turn.role is TurnRole.patient:
            key = question_keys.get(turn.turn_id)
            about = f", answers {key.value}" if key else ""
        else:
            about = f", asks about {turn.question_key.value}" if turn.question_key else ""
        lines.append(f"{label} [{turn.role.value}{about}]: {text}")
    user = "Conversation:\n" + "\n".join(lines)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


__all__ = [
    "PROMPT_VERSION",
    "PROMPT_VERSIONS",
    "build_messages",
    "extractable_keys",
    "prompt_version",
    "registration_spans",
    "template",
    "turn_labels",
]
