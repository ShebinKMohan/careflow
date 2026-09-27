"""`LayaUrgencyModel`: the fine-tuned `laya-ktas` checkpoint behind the M1-05 `UrgencyModel` port.

It wraps `laya.Agent` (which applies the calibrated temperature) and returns an
`UrgencySuggestion` with the KTAS level, its calibrated probability and the top 3 leave-one-fact-out
reasons. Only a suggestion: the rules run first and the nurse confirms every level (safety rules 1
and 3). Like the baseline, it makes no suggestion for a patient known to be under 15: the KTAS data
are adults.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from careflow_contracts import StructuredFacts
from careflow_contracts.generated.urgency_suggestion_schema import TriageScale, UrgencySuggestion
from careflow_triage.inference import OutOfScopeError, out_of_scope_reason
from careflow_triage.laya_ktas.explain import explain
from careflow_triage.laya_ktas.serialize import facts_to_state, level_of
from careflow_triage.laya_ktas.train import MODEL_ID
from careflow_triage.scales import display_label


def default_device() -> str:
    import torch  # type: ignore[import-not-found]

    return "mps" if torch.backends.mps.is_available() else "cpu"


@dataclass
class LayaUrgencyModel:
    agent: Any
    model_version: str
    model_id: str = MODEL_ID

    @classmethod
    def load(cls, path: Path, *, device: str | None = None) -> LayaUrgencyModel:
        import laya  # type: ignore[import-not-found]

        meta_file = path / "meta.json"
        run = (
            json.loads(meta_file.read_text(encoding="utf-8"))["run_id"]
            if meta_file.exists()
            else path.name
        )
        agent = laya.Agent(str(path), device=device or default_device())
        return cls(agent=agent, model_version=str(run))

    def predict(
        self,
        facts: StructuredFacts,
        *,
        scale: TriageScale = TriageScale.KTAS,
        now: datetime | None = None,
    ) -> UrgencySuggestion:
        if (reason := out_of_scope_reason(facts)) is not None:
            raise OutOfScopeError(reason)
        laya_input = facts_to_state(facts)
        result = explain(self.agent, laya_input)
        level = level_of(result.choice)
        digest = hashlib.sha256(
            f"{facts.session_id}|{self.model_version}|{json.dumps(laya_input.state)}".encode()
        ).hexdigest()
        return UrgencySuggestion.model_validate(
            {
                "suggestion_id": f"sug_{digest[:24]}",
                "session_id": facts.session_id,
                "level": level,
                "scale": scale.value,
                "display_label": display_label(level, scale.value),
                "confidence": float(result.probabilities[result.choice]),
                "reasons": [{"text": r.text, "fact_ids": r.fact_ids} for r in result.reasons],
                "model_id": self.model_id,
                "model_version": self.model_version,
                "created_at": (now or datetime.now(UTC)).isoformat(),
            }
        )


__all__ = ["LayaUrgencyModel", "default_device"]
