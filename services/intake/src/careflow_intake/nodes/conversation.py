"""ask, await_answer and extract: the patient conversation loop."""

from __future__ import annotations

import logging
from typing import Any, Final, Literal

from langgraph.types import interrupt

from careflow_contracts.generated.structured_facts_schema import FactKey, Turn, TurnRole
from careflow_intake.nodes import Update
from careflow_intake.ports import IntakePorts
from careflow_intake.state import IntakeSettings, IntakeState

logger = logging.getLogger(__name__)

MAX_ANSWER_CHARS: Final = 4000  # the contract's Turn.text limit


class InvalidAnswer(ValueError):
    """The patient's answer cannot become a turn (empty or too long)."""


def answer_text(raw: Any) -> str:
    """The answer from a resume value: a string, or `{"text": ...}`."""
    text = raw.get("text") if isinstance(raw, dict) else raw
    if not isinstance(text, str) or not text.strip():
        raise InvalidAnswer("the answer is empty")
    text = text.strip()
    if len(text) > MAX_ANSWER_CHARS:
        raise InvalidAnswer(f"the answer is longer than {MAX_ANSWER_CHARS} characters")
    return text


def next_turn_id(state: IntakeState) -> str:
    return f"trn_{len(state.get('turns', [])) + 1:03d}"


async def ask(state: IntakeState, ports: IntakePorts, settings: IntakeSettings) -> Update:
    """Store the next question and its assistant turns (a notice first, if any), or mark the
    conversation complete."""
    question = await ports.conversation.next_question(state)
    if question is None:
        return {"pending_question": None, "conversation_complete": True, "node_trace": ["ask"]}
    now, number = ports.clock(), len(state.get("turns", [])) + 1
    texts: list[tuple[str, FactKey | None]] = [(question.notice, None)] if question.notice else []
    texts.append((question.text, question.key))
    turns = [
        Turn(
            turn_id=f"trn_{number + offset:03d}",
            role=TurnRole.assistant,
            text=text,
            question_key=key,
            created_at=now,
        )
        for offset, (text, key) in enumerate(texts)
    ]
    return {"pending_question": question, "turns": turns, "node_trace": ["ask"]}


def route_after_ask(state: IntakeState) -> Literal["await_answer", "run_rules"]:
    return "await_answer" if state.get("pending_question") is not None else "run_rules"


async def await_answer(state: IntakeState, ports: IntakePorts, settings: IntakeSettings) -> Update:
    """Wait for the patient, then append their turn. Nothing happens before `interrupt()`; an
    invalid answer asks again with the error instead of failing the run."""
    question = state.get("pending_question")
    if question is None:
        raise RuntimeError("await_answer needs a pending question")
    base = {
        "kind": "patient_input",
        "session_id": state["session_id"],
        "question": question.model_dump(mode="json"),
    }
    payload = base
    while True:
        raw = interrupt(payload)
        try:
            text = answer_text(raw)
            break
        except InvalidAnswer as exc:
            payload = {**base, "error": str(exc)}
    turn = Turn(
        turn_id=next_turn_id(state),
        role=TurnRole.patient,
        text=text,
        question_key=question.key,
        created_at=ports.clock(),
    )
    return {
        "turns": [turn],
        "turn_count": state.get("turn_count", 0) + 1,
        "pending_question": None,
        "node_trace": ["await_answer"],
    }


async def extract(state: IntakeState, ports: IntakePorts, settings: IntakeSettings) -> Update:
    """Facts from the transcript after the patient's answer. A failed extraction is not fatal: the
    rules still run on what is known and the nurse sees `extraction_incomplete` (CONVENTIONS)."""
    turns = state.get("turns", [])
    if not turns or turns[-1].role is not TurnRole.patient:
        raise RuntimeError("extract needs the patient's turn")
    question_keys = {
        turn.turn_id: turn.question_key
        for turn in turns
        if turn.role is TurnRole.patient and turn.question_key is not None
    }
    try:
        result = await ports.extractor.extract(turns, state.get("facts", []), question_keys)
    except Exception as exc:  # fail safe
        logger.warning("extraction failed for %s: %s", state["session_id"], type(exc).__name__)
        return {"flags": {"extraction_incomplete": True}, "node_trace": ["extract"]}
    update: Update = {
        "facts": list(result.facts),
        "extraction": {
            "dropped_count": result.dropped_count,
            "model_id": result.model_id,
            "prompt_version": result.prompt_version,
        },
        "node_trace": ["extract"],
    }
    if result.extraction_incomplete:
        update["flags"] = {"extraction_incomplete": True}
    return update
