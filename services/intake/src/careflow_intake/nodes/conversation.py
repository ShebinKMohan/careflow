"""ask, await_answer and extract: the patient conversation loop.

After a side question (M2-10, `nodes.side_questions`), `ask` shows the reply as the next question's
notice, and a turn that was only a question gets the same question again, word for word; such a
turn is not passed to the extractor as an answer to the question on screen.
"""

from __future__ import annotations

import logging
from typing import Any, Final, Literal

from langgraph.types import interrupt

from careflow_contracts.generated.fact_schema import TriState, TriStateFact
from careflow_contracts.generated.structured_facts_schema import FactKey, Language, Turn, TurnRole
from careflow_intake.conversation.child_questions import CHILD_RED, MAX_ASKS, asked_counts
from careflow_intake.conversation.picker import answered_keys
from careflow_intake.facts import FactEntry
from careflow_intake.nodes import Update
from careflow_intake.patient_details import AGE_KEYS
from careflow_intake.ports import IntakePorts, Question
from careflow_intake.state import IntakeSettings, IntakeState, structured_facts
from careflow_intake.tap import answer_fact, explicit_fact, tap_fact

logger = logging.getLogger(__name__)

MAX_ANSWER_CHARS: Final = 4000  # the contract's Turn.text limit
MAX_NOTICE_CHARS: Final = 300  # the contract's IntakeQuestion.notice limit


def fit(text: str, limit: int = MAX_NOTICE_CHARS) -> str:
    """The text, cut to `limit` characters with an ellipsis."""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class InvalidAnswer(ValueError):
    """The patient's answer cannot become a turn (empty or too long)."""


def answer_mode(raw: Any) -> str:
    """How the answer came (`{"input_mode": ...}`): "tap", "voice" or, by default, "text"."""
    mode = raw.get("input_mode") if isinstance(raw, dict) else None
    return mode if mode in ("tap", "text", "voice") else "text"


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
    conversation complete. A turn that was only a question gets the question it interrupted
    again, word for word; a side question's reply becomes the notice (M2-10)."""
    intent, answered = state.get("turn_intent"), state.get("answered_question")
    # Asked again only while its fact is still missing: a turn the router took for a question can
    # still have answered it ("I have chest pain, is it serious?" gives the complaint).
    again = (
        intent is not None
        and not intent.intake_answer
        and answered is not None
        and (answered.key is None or answered.key not in answered_keys(structured_facts(state)))
    )
    if again and answered is not None:
        question: Question | None = answered.model_copy(
            update={"notice": None, "notice_kind": None, "citations": []}
        )
    else:
        question = await ports.conversation.next_question(state)
    if question is None:  # a side question's reply stays in the state for the done text
        return {
            "pending_question": None,
            "conversation_complete": True,
            "turn_intent": None,
            "node_trace": ["ask"],
        }
    question = with_reply(question, state)
    now, number = ports.clock(), len(state.get("turns", [])) + 1
    texts: list[tuple[str, FactKey | None]] = [(question.notice, None)] if question.notice else []
    # Asked again: not another ask of its key (each key is asked at most twice, M1-07).
    texts.append((question.text, None if again else question.key))
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
    return {
        "pending_question": question,
        "turns": turns,
        "turn_intent": None,
        "reply_prefix": None,
        "reply_kind": None,
        "reply_citations": [],
        "node_trace": ["ask"],
    }


def with_reply(question: Question, state: IntakeState) -> Question:
    """The question with a side question's reply before any notice it has; a notice without a
    reply is the nurse-coming message."""
    prefix = state.get("reply_prefix")
    if not prefix:
        if question.notice and question.notice_kind is None:
            return question.model_copy(update={"notice_kind": "nurse_coming"})
        return question
    notice = f"{prefix}\n\n{question.notice}" if question.notice else prefix
    return question.model_copy(
        update={
            "notice": fit(notice),
            "notice_kind": state.get("reply_kind"),
            "citations": list(state.get("reply_citations") or []),
        }
    )


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
    mode = answer_mode(raw)
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
        "answered_question": question,
        "last_answer_mode": mode,
        "node_trace": ["await_answer"],
    }


async def extract(state: IntakeState, ports: IntakePorts, settings: IntakeSettings) -> Update:
    """Facts from the transcript after the patient's answer. A failed extraction is not fatal: the
    rules still run on what is known and the nurse sees `extraction_incomplete` (CONVENTIONS).

    An answer read without a model comes first (M2-04, SHE-293): a tapped label, or an explicit
    yes / no / not sure in words to a yes/no question. An explicit answer that says nothing more
    needs no extractor; one that says more ("Yes, since this morning") still goes to it, for the
    other facts it may hold, and keeps its own reading of the question unless the extractor found
    a yes to an explicit "not sure". A child's red question that no answer could be read for is
    recorded as unknown after its last ask (`unread_red`), never as no."""
    turns = state.get("turns", [])
    if not turns or turns[-1].role is not TurnRole.patient:
        raise RuntimeError("extract needs the patient's turn")
    only_questions = set(state.get("side_turn_ids", []))  # M2-10: not answers to the question
    question_keys = {
        turn.turn_id: turn.question_key
        for turn in turns
        if turn.role is TurnRole.patient
        and turn.question_key is not None
        and turn.turn_id not in only_questions
    }
    latest = turns[-1]
    key = question_keys.get(latest.turn_id)
    explicit: FactEntry | None = None
    if key is not None:
        if state.get("last_answer_mode") == "tap":
            # A tapped Yes / No / Not sure or scale number maps straight to its fact (M2-04).
            tapped = tap_fact(latest, key)
            if tapped is not None:
                return {"facts": [tapped], "node_trace": ["extract"]}
        found = explicit_fact(latest, key)
        if found is not None:
            explicit, whole = found
            if whole:
                return {"facts": [explicit], "node_trace": ["extract"]}
    try:
        result = await ports.extractor.extract(
            turns, state.get("facts", []), question_keys, Language(state["language"])
        )
    except Exception as exc:  # fail safe
        logger.warning("extraction failed for %s: %s", state["session_id"], type(exc).__name__)
        kept = [explicit] if explicit is not None else unread_red(state, latest)
        failed: Update = {"flags": {"extraction_incomplete": True}, "node_trace": ["extract"]}
        return {**failed, "facts": kept} if kept else failed
    facts = list(result.facts)
    if state.get("patient_type") == "child":
        # A child's age is the parent's kiosk entry (M2-04); a model never replaces it.
        facts = [entry for entry in facts if entry.key not in AGE_KEYS]
    incomplete = result.extraction_incomplete
    if explicit is not None:
        facts = with_explicit(facts, explicit)
    elif key is not None and all(entry.key is not key for entry in facts):
        unread = unread_red(state, latest)
        facts += unread
        incomplete = incomplete or bool(unread)
    update: Update = {
        "facts": facts,
        "extraction": {
            "dropped_count": result.dropped_count,
            "model_id": result.model_id,
            "prompt_version": result.prompt_version,
        },
        "node_trace": ["extract"],
    }
    if incomplete:
        update["flags"] = {"extraction_incomplete": True}
    return update


def with_explicit(facts: list[FactEntry], explicit: FactEntry) -> list[FactEntry]:
    """The extractor's facts with the explicit answer to the question on screen. The extractor's
    own answer to that question is kept only when it is a yes to an explicit "not sure": of two
    readings of one answer, the one that alerts sooner wins."""
    same = [entry for entry in facts if entry.key is explicit.key]
    if tristate(explicit) is TriState.unknown and any(tristate(e) is TriState.yes for e in same):
        return facts
    return [*(entry for entry in facts if entry.key is not explicit.key), explicit]


def tristate(entry: FactEntry) -> TriState | None:
    fact = entry.fact
    return fact.value if isinstance(fact, TriStateFact) else None


def unread_red(state: IntakeState, latest: Turn) -> list[FactEntry]:
    """A child's red question (M2-04) that the parent's answer could not be read for: nothing
    after its first ask (the question is asked once more), and `unknown` after its last one,
    citing the parent's own answer, so the rules alert with `parent_unsure` instead of the
    question being left unanswered (SHE-293, ADR-052). Never `no`; amber questions need a yes to
    alert, so nothing is recorded for them."""
    key = latest.question_key
    if state.get("patient_type") != "child" or key is None or key not in CHILD_RED:
        return []
    if key in answered_keys(structured_facts(state)):
        return []
    if asked_counts(state.get("turns", [])).get(key, 0) < MAX_ASKS:
        return []
    entry = answer_fact(latest, key, TriState.unknown.value)
    return [] if entry is None else [entry]
