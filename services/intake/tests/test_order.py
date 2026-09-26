"""Safety rules 1 and 3 by construction: the suggestion needs run_rules, and nothing after the
nurse review runs without it."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from langchain_core.runnables.graph import Edge

from careflow_contracts.generated.structured_facts_schema import StructuredFacts
from careflow_contracts.generated.urgency_suggestion_schema import TriageScale, UrgencySuggestion
from careflow_intake.checkpoint import memory_checkpointer
from careflow_intake.graph import IntakeGraph, answer, build_graph, pending, start_intake
from careflow_intake.stubs import START, RecordingRulesRunner, StubUrgencyModel


def reachable(edges: Iterable[Edge], *, without: str | None = None) -> set[str]:
    """Nodes reachable from START when every edge touching `without` is removed."""
    nexts: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        if without not in (edge.source, edge.target):
            nexts[edge.source].add(edge.target)
    seen, stack = {"__start__"}, ["__start__"]
    while stack:
        for target in nexts[stack.pop()] - seen:
            seen.add(target)
            stack.append(target)
    return seen


def test_suggest_urgency_is_unreachable_without_run_rules(graph: IntakeGraph) -> None:
    edges = graph.get_graph().edges
    assert "suggest_urgency" in reachable(edges)
    assert "suggest_urgency" not in reachable(edges, without="run_rules")
    assert {edge.source for edge in edges if edge.target == "suggest_urgency"} == {"run_rules"}


def test_nothing_after_the_nurse_review_is_reachable_without_it(graph: IntakeGraph) -> None:
    after = {"assign_token", "writeback", "build_briefing", "__end__"}
    edges = graph.get_graph().edges
    assert after <= reachable(edges)
    assert not after & reachable(edges, without="nurse_review")


class RecordingUrgencyModel(StubUrgencyModel):
    seen: list[StructuredFacts]

    def __init__(self) -> None:
        super().__init__()
        self.seen = []

    def predict(
        self, facts: StructuredFacts, *, scale: TriageScale = TriageScale.KTAS, now: Any = None
    ) -> UrgencySuggestion:
        self.seen.append(facts)
        return super().predict(facts, scale=scale, now=now)


async def test_rules_run_on_the_final_facts_before_the_suggestion(
    make_ports: Any, script: dict[str, Any]
) -> None:
    rules, urgency = RecordingRulesRunner(), RecordingUrgencyModel()
    graph = build_graph(make_ports(rules=rules, urgency=urgency), memory_checkpointer())
    result = await start_intake(
        graph, session_id=script["session_id"], clinic_id=script["clinic_id"], arrived_at=START
    )
    for text in script["answers"]:
        result = await answer(graph, script["session_id"], text)
    assert (pending(result) or {})["kind"] == "nurse_review"
    trace = result["node_trace"]
    first = trace.index("suggest_urgency")
    assert trace[first - 1] == "run_rules" and "run_rules" in trace[:first]
    assert trace.count("suggest_urgency") == 1
    # The model saw exactly the facts the rules last evaluated.
    assert urgency.seen == [rules.seen[-1]]
