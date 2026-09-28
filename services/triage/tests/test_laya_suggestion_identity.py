"""Fresh source evidence creates a new suggestion identity even for equal vital values."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careflow_contracts import StructuredFacts
from careflow_contracts.ids import stable_fact_id
from careflow_triage.laya_ktas.model import LayaUrgencyModel


class Agent:
    def predict_batch(self, states: list[dict[str, str]], question: Any) -> list[dict[str, Any]]:
        return [
            {"answers": {"urgency": {"choice": "level_2", "probabilities": {"level_2": 0.6}}}}
            for _ in states
        ]


def test_laya_suggestion_id_binds_the_current_fact_sources() -> None:
    path = (
        Path(__file__).resolve().parents[3]
        / "packages/contracts/fixtures/valid/chest_pain.structured-facts.json"
    )
    facts = StructuredFacts.model_validate_json(path.read_text())
    model = LayaUrgencyModel(agent=Agent(), model_version="test-source-identity")
    first = model.predict(facts)
    assert model.predict(facts).suggestion_id == first.suggestion_id
    assert facts.vitals is not None and facts.vitals.heart_rate is not None
    fresh_hr = facts.vitals.heart_rate.model_copy(
        update={"fact_id": stable_fact_id(facts.session_id, "new-device-reading", "heart_rate")}
    )
    fresh = facts.model_copy(
        update={"vitals": facts.vitals.model_copy(update={"heart_rate": fresh_hr})}
    )
    assert model.predict(fresh).suggestion_id != first.suggestion_id
