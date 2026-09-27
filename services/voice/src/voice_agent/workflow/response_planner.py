from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ResponsePlanAction = Literal[
    "streaming_llm",
    "fast_path_text",
    "fast_path_audio",
    "clarify",
    "transfer_or_escalate",
    "hold_ack_then_llm",
]


@dataclass(frozen=True, slots=True)
class ResponsePlan:
    action: ResponsePlanAction
    confidence: float
    reason: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0.")
        if not self.reason.strip():
            raise ValueError("reason must not be blank.")


class ResponsePlanner:
    """Chooses the response path for a committed caller turn.

    The initial planner is behavior-preserving: every turn still goes through the
    existing streaming LLM path. Keeping this decision explicit gives the workflow
    a stable seam for future fast paths, cached audio, and escalation logic.
    """

    def plan(self, *, user_text: str) -> ResponsePlan:
        clean_text = user_text.strip()
        if not clean_text:
            return ResponsePlan(
                action="clarify",
                confidence=1.0,
                reason="blank_turn",
            )
        return ResponsePlan(
            action="streaming_llm",
            confidence=1.0,
            reason="default_open_ended_path",
        )
