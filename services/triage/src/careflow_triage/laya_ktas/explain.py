"""Leave-one-fact-out reasons for a Laya suggestion (M3-01).

Each state key is dropped in turn; its score is p(predicted level | full state) minus
p(predicted level | state without the key). The top 3 positive scores are the reasons ("Heart rate
128 /min made KTAS 2 more likely"), each citing its facts. With no positive score, the key whose
absence changes the probability most stands alone, so a suggestion always has a reason. Staff only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from careflow_triage.laya_ktas.serialize import (
    KTAS_QUESTION,
    LayaInput,
    Reason,
    reasons_from_keys,
)


@dataclass(frozen=True)
class Explained:
    choice: str  # level_1 ... level_5
    probabilities: dict[str, float]
    reasons: list[Reason]
    scores: dict[str, float]


def urgency(result: dict[str, Any]) -> tuple[str, dict[str, float]]:
    answer = result["answers"]["urgency"]
    probabilities = {str(k): float(v) for k, v in answer["probabilities"].items()}
    return str(answer["choice"]), probabilities


def explain(agent: Any, laya_input: LayaInput, k: int = 3) -> Explained:
    """The prediction and its top-k reasons, in one batched call."""
    keys = list(laya_input.state)
    states = [laya_input.state] + [laya_input.without(key).state for key in keys]
    results = agent.predict_batch(states, KTAS_QUESTION)
    choice, probabilities = urgency(results[0])
    full = probabilities[choice]
    scores = {
        key: full - urgency(result)[1].get(choice, 0.0)
        for key, result in zip(keys, results[1:], strict=True)
    }
    ranked = sorted(keys, key=lambda key: (-scores[key], keys.index(key)))
    top = [key for key in ranked if scores[key] > 0][:k]
    if not top and ranked:
        top = [max(keys, key=lambda key: (abs(scores[key]), -keys.index(key)))]
    return Explained(
        choice=choice,
        probabilities=probabilities,
        reasons=reasons_from_keys(top, laya_input, choice=choice, scores=scores),
        scores=scores,
    )


def top_reasons(agent: Any, laya_input: LayaInput, k: int = 3) -> list[Reason]:
    return explain(agent, laya_input, k).reasons


__all__ = ["Explained", "explain", "top_reasons", "urgency"]
