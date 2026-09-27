"""`build_graph`: the intake StateGraph, and the helpers the API uses to drive it.

    START -> identify -> fetch_ehr_context -> ask
    ask -> await_answer -> intent_router -> extract -> run_rules   (a question was asked)
    ask -> run_rules                                  (the conversation is complete)
    run_rules -> handle_side_question -> run_rules    (the patient asked something, M2-10)
    run_rules -> ask | suggest_urgency                (route_after_rules)
    suggest_urgency -> nurse_review -> assign_token -> writeback -> build_briefing -> END

`suggest_urgency` is reachable only through `run_rules` (safety rule 1), and nothing after
`nurse_review` runs until a nurse resumes the graph (safety rule 3). Every patient turn reaches
`extract` and `run_rules`, whatever the intent router finds in it. `thread_id` = session id.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Final

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, StateSnapshot

from careflow_contracts.generated.nurse_decision_schema import NurseDecision
from careflow_contracts.generated.structured_facts_schema import Language
from careflow_intake.facts import FactEntry, as_entry
from careflow_intake.nodes import NodeFn, Update
from careflow_intake.nodes.closing import assign_token, build_briefing, writeback
from careflow_intake.nodes.conversation import (
    answer_text,
    ask,
    await_answer,
    extract,
    route_after_ask,
)
from careflow_intake.nodes.patient import fetch_ehr_context, identify
from careflow_intake.nodes.rules import run_rules
from careflow_intake.nodes.side_questions import (
    handle_side_question,
    intent_router,
    route_after_run_rules,
)
from careflow_intake.nodes.triage import nurse_review, suggest_urgency, validate_decision
from careflow_intake.ports import IntakePorts
from careflow_intake.state import IntakeSettings, IntakeState

type IntakeGraph = CompiledStateGraph[IntakeState, None, IntakeState, IntakeState]

NODES: Final[dict[str, NodeFn]] = {
    "identify": identify,
    "fetch_ehr_context": fetch_ehr_context,
    "ask": ask,
    "await_answer": await_answer,
    "intent_router": intent_router,
    "extract": extract,
    "run_rules": run_rules,
    "handle_side_question": handle_side_question,
    "suggest_urgency": suggest_urgency,
    "nurse_review": nurse_review,
    "assign_token": assign_token,
    "writeback": writeback,
    "build_briefing": build_briefing,
}


class NotWaiting(RuntimeError):
    """The session's graph is not waiting for this input."""


def _bind(name: str, fn: NodeFn, ports: IntakePorts, settings: IntakeSettings) -> Any:
    async def node(state: IntakeState) -> Update:
        return await fn(state, ports, settings)

    node.__name__ = name
    return node


def build_graph(
    ports: IntakePorts,
    checkpointer: BaseCheckpointSaver[Any],
    *,
    settings: IntakeSettings | None = None,
) -> IntakeGraph:
    settings = settings or IntakeSettings()
    graph = StateGraph(IntakeState)
    for name, fn in NODES.items():
        graph.add_node(name, _bind(name, fn, ports, settings))
    graph.add_edge(START, "identify")
    graph.add_edge("identify", "fetch_ehr_context")
    graph.add_edge("fetch_ehr_context", "ask")
    # Path maps name every possible target, so get_graph() shows the exact edges.
    graph.add_conditional_edges(
        "ask", route_after_ask, {"await_answer": "await_answer", "run_rules": "run_rules"}
    )
    graph.add_edge("await_answer", "intent_router")
    graph.add_edge("intent_router", "extract")
    graph.add_edge("extract", "run_rules")
    graph.add_conditional_edges(
        "run_rules",
        route_after_run_rules,
        {
            "handle_side_question": "handle_side_question",
            "ask": "ask",
            "suggest_urgency": "suggest_urgency",
        },
    )
    # Back through the rules, which stay the only way to the suggestion (safety rule 1).
    graph.add_edge("handle_side_question", "run_rules")
    graph.add_edge("suggest_urgency", "nurse_review")
    graph.add_edge("nurse_review", "assign_token")
    graph.add_edge("assign_token", "writeback")
    graph.add_edge("writeback", "build_briefing")
    graph.add_edge("build_briefing", END)
    return graph.compile(checkpointer=checkpointer)


def thread_config(session_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": session_id}}


def pending(result: dict[str, Any] | StateSnapshot) -> dict[str, Any] | None:
    """The value of the interrupt the graph is paused at, or None."""
    interrupts = (
        result.interrupts if isinstance(result, StateSnapshot) else result.get("__interrupt__", ())
    )
    return dict(interrupts[0].value) if interrupts else None


async def start_intake(
    graph: IntakeGraph,
    *,
    session_id: str,
    clinic_id: str,
    arrived_at: datetime,
    language: Language = Language.en,
) -> dict[str, Any]:
    """Run a new intake until its first pause (a patient question or the nurse review)."""
    state: IntakeState = {
        "session_id": session_id,
        "clinic_id": clinic_id,
        "language": language,
        "arrived_at": arrived_at,
    }
    return await graph.ainvoke(state, thread_config(session_id))


def waiting_at(snapshot: StateSnapshot, node: str) -> bool:
    """Paused at `node`: it is next (e.g. after `aupdate_state`), or its task holds an interrupt
    (after it asked again, LangGraph 1.2 reports no `next` but keeps the interrupted task)."""
    return node in snapshot.next or any(
        task.name == node and task.interrupts for task in snapshot.tasks
    )


async def _waiting_at(graph: IntakeGraph, session_id: str, node: str) -> StateSnapshot:
    snapshot = await graph.aget_state(thread_config(session_id))
    if not waiting_at(snapshot, node):
        raise NotWaiting(f"session {session_id} is not waiting at {node}")
    return snapshot


async def answer(
    graph: IntakeGraph, session_id: str, text: str, input_mode: str = "text"
) -> dict[str, Any]:
    """Resume with the patient's answer and how it came (tap, text or voice). Raises
    `InvalidAnswer` before touching the graph."""
    await _waiting_at(graph, session_id, "await_answer")
    resume = {"text": answer_text(text), "input_mode": input_mode}
    return await graph.ainvoke(Command(resume=resume), thread_config(session_id))


async def decide(
    graph: IntakeGraph,
    session_id: str,
    decision: NurseDecision | dict[str, Any],
    *,
    settings: IntakeSettings | None = None,
) -> dict[str, Any]:
    """Resume with the nurse's decision. An invalid decision raises `InvalidNurseDecision` and
    leaves the graph waiting, so a later valid decision still goes through."""
    settings = settings or IntakeSettings()
    snapshot = await _waiting_at(graph, session_id, "nurse_review")
    values: IntakeState = snapshot.values  # type: ignore[assignment]
    valid = validate_decision(decision, values, scale=settings.scale)
    return await graph.ainvoke(
        Command(resume=valid.model_dump(mode="json")), thread_config(session_id)
    )


async def add_facts(
    graph: IntakeGraph, session_id: str, facts: Sequence[FactEntry | dict[str, Any]]
) -> None:
    """External facts (device or nurse vitals). The reducer merges them by fact id; the next
    `run_rules` sees them."""
    entries = [as_entry(fact) for fact in facts]
    await graph.aupdate_state(thread_config(session_id), {"facts": entries})


__all__ = [
    "NODES",
    "IntakeGraph",
    "NotWaiting",
    "add_facts",
    "answer",
    "build_graph",
    "decide",
    "pending",
    "start_intake",
    "thread_config",
    "waiting_at",
]
