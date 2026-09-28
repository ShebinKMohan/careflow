"""Leave-one-fact-out reasons explain the predicted class, including low-acuity classes."""

from __future__ import annotations

from typing import Any

from careflow_triage.laya_ktas.explain import explain
from careflow_triage.laya_ktas.serialize import LayaInput


class Agent:
    def __init__(self, probabilities: list[float]) -> None:
        self.probabilities = probabilities

    def predict_batch(self, states: list[dict[str, str]], question: Any) -> list[dict[str, Any]]:
        assert len(states) == len(self.probabilities)
        return [
            {"answers": {"urgency": {"choice": "level_5", "probabilities": {"level_5": p}}}}
            for p in self.probabilities
        ]


def test_laya_support_for_low_acuity_is_never_described_as_raised_urgency() -> None:
    facts = LayaInput({"heart_rate": "72 /min"}, {"heart_rate": ["fact_hr"]})
    result = explain(Agent([0.8, 0.5]), facts)
    assert result.reasons[0].text == "Heart rate 72 /min made KTAS 5 more likely"
    assert result.reasons[0].fact_ids == ["fact_hr"]


def test_laya_negative_fallback_retains_the_effect_direction() -> None:
    facts = LayaInput({"heart_rate": "128 /min"}, {"heart_rate": ["fact_hr"]})
    result = explain(Agent([0.5, 0.8]), facts)
    assert result.reasons[0].text == "Heart rate 128 /min made KTAS 5 less likely"
    assert result.reasons[0].fact_ids == ["fact_hr"]


def test_laya_zero_effect_does_not_claim_a_direction() -> None:
    facts = LayaInput({"heart_rate": "72 /min"}, {"heart_rate": ["fact_hr"]})
    result = explain(Agent([0.5, 0.5]), facts)
    assert result.reasons[0].text == "Heart rate 72 /min was included in the model input"
