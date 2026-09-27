"""The versioned extraction prompt and the messages sent to the model."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import cache
from pathlib import Path
from typing import Final

from careflow_contracts.generated.fact_schema import IntegerFact, TextFact, TriStateFact
from careflow_contracts.generated.structured_facts_schema import FactKey, Symptom, Turn, TurnRole
from careflow_intake.facts import fact_class
from careflow_providers.base import Message

PROMPT_VERSION: Final = "extraction_v2"
PROMPT_DIR: Final = Path(__file__).parent / "prompts"


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


def build_messages(turns: Sequence[Turn], question_keys: Mapping[str, FactKey]) -> list[Message]:
    """A system message with the rules and allowed keys, and the transcript with each turn's label,
    role and question key."""
    system = template().replace("{keys}", ", ".join(key.value for key in extractable_keys()))
    lines = []
    for label, turn in turn_labels(turns).items():
        if turn.role is TurnRole.patient:
            key = question_keys.get(turn.turn_id)
            about = f", answers {key.value}" if key else ""
        else:
            about = f", asks about {turn.question_key.value}" if turn.question_key else ""
        lines.append(f"{label} [{turn.role.value}{about}]: {turn.text}")
    user = "Conversation:\n" + "\n".join(lines)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


__all__ = ["PROMPT_VERSION", "build_messages", "extractable_keys", "template", "turn_labels"]
