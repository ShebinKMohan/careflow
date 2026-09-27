"""intent_router and handle_side_question: questions the patient asks mid-intake (M2-10).

    await_answer -> intent_router -> extract -> run_rules
    run_rules -> handle_side_question -> run_rules      (a side question waits for its reply)

`intent_router` only records what the turn holds (`TurnIntent`, multi-label); it never calls
`interrupt()` and never keeps a turn from `extract` and `run_rules`, so symptoms inside a question
still become facts and alerts (safety rule 1). A turn that is only a question does not count
towards the 8-answer cap, but the 120 s timer keeps running. Taps are always answers.

`handle_side_question` runs after the rules, so an alert is never held up by a side question, and
returns to `run_rules`, which stays the only way to the suggestion:

- a medical question is kept for the nurse (`nurse.flag`, reason `medical_question_intake`, every
  time, even over the budget) and gets the fixed refusal: never an answer (safety rule 2);
- a clinic question on the phone gets the ask-the-clinic agent's answer (M2-09), cut to 2
  sentences and checked by the output filter; on the kiosk it gets "ask this on your phone", and
  the agent is never called there (ADR-006);
- after 2 side questions, the reply is "let's finish these questions first".

The reply is shown as the notice of the next question: the same question again, word for word,
when the turn was only a question (`ask`).
"""

from __future__ import annotations

import logging
import re
from typing import Final, Literal

from careflow_contracts.generated.structured_facts_schema import Language, TurnRole
from careflow_contracts.generated.turn_intent_schema import TurnIntent
from careflow_intake.conversation.checklist import templates_for
from careflow_intake.conversation.output_filter import check_patient_facing
from careflow_intake.nodes import Update
from careflow_intake.nodes.conversation import fit
from careflow_intake.nodes.rules import route_after_rules
from careflow_intake.ports import IntakePorts, NoticeKind
from careflow_intake.state import IntakeSettings, IntakeState

logger = logging.getLogger(__name__)

SIDE_QUESTION_BUDGET: Final = 2
ANSWER_SENTENCES: Final = 2
ANSWER_ONLY: Final = TurnIntent(intake_answer=True, clinic_question=False, medical_question=False)
#: A classifier that fails treats the turn as an answer and a medical question (fail safe).
FAIL_SAFE: Final = TurnIntent(intake_answer=True, clinic_question=False, medical_question=True)
_SENTENCE_END: Final = re.compile(r"(?<=[.!?؟。])\s+")


def first_sentences(text: str, count: int = ANSWER_SENTENCES) -> str:
    sentences = [part for part in _SENTENCE_END.split(" ".join(text.split())) if part]
    return " ".join(sentences[:count])


async def intent_router(state: IntakeState, ports: IntakePorts, settings: IntakeSettings) -> Update:
    """What the latest patient turn holds. Only records it: extraction and the rules run next on
    every turn, whatever it holds."""
    turns = state.get("turns", [])
    if not turns or turns[-1].role is not TurnRole.patient:
        raise RuntimeError("intent_router needs the patient's turn")
    latest = turns[-1]
    update: Update = {"node_trace": ["intent_router"]}
    if state.get("last_answer_mode") == "tap":  # a tapped choice always answers the question
        intent, category = ANSWER_ONLY, None
    else:
        asked = state.get("answered_question")
        try:
            classified = await ports.intents.classify(
                latest.text, Language(state["language"]), asked.text if asked else None
            )
            intent, category = classified.intent, classified.category
        except Exception as exc:  # the port must not raise; if it does, fail safe
            logger.warning("intent classification failed: %s", type(exc).__name__)
            intent, category = FAIL_SAFE, "fail_safe"
    update["turn_intent"] = intent
    update["side_question_pending"] = intent.clinic_question or intent.medical_question
    update["side_category"] = category if intent.medical_question else None
    if not intent.intake_answer:
        # Only a question: not one of the 8 answers (the timer keeps running), and the extractor
        # is not told it answered the question on screen.
        update["turn_count"] = max(0, state.get("turn_count", 0) - 1)
        update["side_turn_ids"] = [latest.turn_id]
    return update


def route_after_run_rules(
    state: IntakeState,
) -> Literal["handle_side_question", "ask", "suggest_urgency"]:
    """A waiting side question gets its reply first; then on as `route_after_rules`."""
    if state.get("side_question_pending", False):
        return "handle_side_question"
    return route_after_rules(state)


async def handle_side_question(
    state: IntakeState, ports: IntakePorts, settings: IntakeSettings
) -> Update:
    """Flag a medical question, answer a clinic question (phone only) or point to the phone, and
    keep the reply for the next question's notice."""
    intent = state.get("turn_intent") or ANSWER_ONLY
    latest = next(
        turn for turn in reversed(state.get("turns", [])) if turn.role is TurnRole.patient
    )
    language = Language(state["language"])
    messages = templates_for(language).messages
    handled = state.get("side_questions", 0)
    over_budget = handled >= SIDE_QUESTION_BUDGET
    kind: NoticeKind
    citations: list[dict[str, str]] = []
    if intent.medical_question:
        # Every time, even over the budget: the nurse hears of each medical question.
        await ports.nurse_flags.flag(
            session_id=state["session_id"],
            clinic_id=state["clinic_id"],
            turn_id=latest.turn_id,
            question=latest.text[:500],
            language=language,
            category=state.get("side_category") or "fail_safe",
        )
        kind = "finish_first" if over_budget else "medical_refusal"
        reply = messages[kind]
    elif over_budget:
        kind, reply = "finish_first", messages["finish_first"]
    elif state.get("channel") == "phone":
        kind, reply, citations = await _clinic_answer(state, ports, latest.text, language)
    else:  # the kiosk never answers from the handbook (ADR-006): the phone does
        kind, reply = "ask_on_phone", messages["ask_on_phone"]
    return {
        "side_question_pending": False,
        "side_questions": handled + 1,
        "reply_prefix": fit(reply),
        "reply_kind": kind,
        "reply_citations": citations,
        "node_trace": ["handle_side_question"],
    }


async def _clinic_answer(
    state: IntakeState, ports: IntakePorts, question: str, language: Language
) -> tuple[NoticeKind, str, list[dict[str, str]]]:
    messages = templates_for(language).messages
    try:
        reply = await ports.clinic_qa.ask(
            session_id=state["session_id"],
            clinic_id=state["clinic_id"],
            question=question,
            language=language,
        )
    except Exception as exc:  # no answer is better than a stalled intake
        logger.warning("clinic question failed: %s", type(exc).__name__)
        return "clinic_answer", messages["dont_know"], []
    if reply.refused_medical:  # the agent judged it medical: its own flag already went out
        return "medical_refusal", messages["medical_refusal"], []
    text = first_sentences(reply.answer)
    if reply.dont_know or not text or not check_patient_facing(text).allowed:
        return "clinic_answer", messages["dont_know"], []
    return "clinic_answer", text, list(reply.citations)


__all__ = [
    "ANSWER_ONLY",
    "FAIL_SAFE",
    "SIDE_QUESTION_BUDGET",
    "first_sentences",
    "handle_side_question",
    "intent_router",
    "route_after_run_rules",
]
