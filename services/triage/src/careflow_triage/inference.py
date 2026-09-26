"""`UrgencyModel`: load a trained artifact and suggest a level for StructuredFacts (M1-05 port).

The suggestion carries the calibrated confidence and the top three SHAP reasons for the
predicted level (LinearExplainer for lr, TreeExplainer for hgb). It is only a suggestion: the
rules run first and the nurse confirms every level. The KTAS data are adults (15 and over), so
a patient known to be younger gets no suggestion (`OutOfScopeError`).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import joblib
import numpy as np
import shap
from threadpoolctl import ThreadpoolController

from careflow_contracts import StructuredFacts
from careflow_contracts.generated.urgency_suggestion_schema import TriageScale, UrgencySuggestion
from careflow_triage.calibrate import probabilities
from careflow_triage.ktas.features import (
    ACVPU_CODES,
    ARRIVAL_CODES,
    FEATURE_NAMES,
    complaint_codes,
    facts_to_features,
    feature_fact_ids,
    mapping,
)
from careflow_triage.scales import display_label

LABELS: Final = {
    "age_years": ("Age", "{:.0f}"),
    "sex_male": ("Sex", None),
    "arrival_mode": ("Arrival", None),
    "injury": ("Injury", None),
    "acvpu": ("Consciousness (ACVPU)", None),
    "pain_present": ("Pain", None),
    "pain_score": ("Pain score", "{:.0f}/10"),
    "systolic_bp": ("Systolic BP", "{:.0f} mmHg"),
    "diastolic_bp": ("Diastolic BP", "{:.0f} mmHg"),
    "heart_rate": ("Heart rate", "{:.0f}/min"),
    "respiratory_rate": ("Respiratory rate", "{:.0f}/min"),
    "temperature_c": ("Temperature", "{:.1f} °C"),
    "spo2": ("SpO2", "{:.0f}%"),
    "complaint_category": ("Complaint", None),
}
MIN_AGE_YEARS: Final = 15  # the KTAS data: adults, youngest 15 years (ADR-019)
ARRIVAL_TEXT: Final = {
    "walking": "walked in",
    "private_vehicle": "private vehicle",
    "public_ambulance": "public ambulance",
    "private_ambulance": "private ambulance",
    "other": "other",
}


def describe(feature: str, value: float) -> str:
    label, fmt = LABELS[feature]
    if math.isnan(value):
        return f"{label} not recorded"
    if fmt is not None:
        return f"{label} {fmt.format(value)}"
    code = int(value)
    if feature == "sex_male":
        return f"{label} {'male' if code == 1 else 'female'}"
    if feature in ("injury", "pain_present"):
        return f"{label} {'yes' if code == 1 else 'no'}"
    if feature == "arrival_mode":
        return f"{label} by {ARRIVAL_TEXT[ARRIVAL_CODES[code]]}"
    if feature == "acvpu":
        return f"{label} {ACVPU_CODES[code]}"
    displays = {rule["code"]: rule["display"] for rule in mapping()["complaint_categories"]}
    return f"{label}: {displays[complaint_codes()[code]].lower()}"


class OutOfScopeError(ValueError):
    """The patient is outside the training population, so the model makes no suggestion."""


def out_of_scope_reason(facts: StructuredFacts) -> str | None:
    """Why the model must not suggest a level for these facts, or None.

    An unknown age is in scope.
    """
    patient = facts.patient
    if patient is None:
        return None
    if patient.age_months is not None and patient.age_months.value < MIN_AGE_YEARS * 12:
        return f"age under {MIN_AGE_YEARS}: the KTAS training data are adults only"
    if patient.age_years is not None and patient.age_years.value < MIN_AGE_YEARS:
        return f"age under {MIN_AGE_YEARS}: the KTAS training data are adults only"
    return None


@dataclass
class UrgencyModel:
    model: Any
    meta: dict[str, Any]
    background: np.ndarray
    _threads: ThreadpoolController = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # Built once the model's libraries (OpenMP) are loaded: building one per call
        # (`threadpool_limits`) costs about 1 ms.
        self._threads = ThreadpoolController()

    @classmethod
    def load(cls, path: Path) -> UrgencyModel:
        meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
        if meta["features"] != list(FEATURE_NAMES):
            raise ValueError("artifact features differ from facts_to_features")
        instance = cls(joblib.load(path / "model.joblib"), meta, np.load(path / "background.npy"))
        instance._build_explainer()
        return instance

    @property
    def model_version(self) -> str:
        return str(self.meta["model_version"])

    def _build_explainer(self) -> None:
        if self.meta["kind"] == "lr":
            pre, classifier = self.model["pre"], self.model["model"]
            self._explainer = shap.LinearExplainer(classifier, pre.transform(self.background))
            names = pre.get_feature_names_out()
            self._columns = np.array([int(re.search(r"x(\d+)", str(n)).group(1)) for n in names])  # type: ignore[union-attr]
        else:
            self._explainer = shap.TreeExplainer(self.model)
            self._columns = np.arange(len(FEATURE_NAMES))

    def contributions(self, x: np.ndarray, class_index: int) -> np.ndarray:
        """SHAP values of each feature towards `class_index` (lr: one-hot columns summed back)."""
        row = self.model["pre"].transform(x) if self.meta["kind"] == "lr" else x
        values = np.asarray(self._explainer.shap_values(row))
        per_column = values[0, :, class_index] if values.ndim == 3 else values[class_index][0]
        totals = np.zeros(len(FEATURE_NAMES))
        np.add.at(totals, self._columns, per_column)
        return totals

    def predict(
        self,
        facts: StructuredFacts,
        *,
        scale: TriageScale = TriageScale.KTAS,
        now: datetime | None = None,
    ) -> UrgencySuggestion:
        if (reason := out_of_scope_reason(facts)) is not None:
            raise OutOfScopeError(reason)
        x = facts_to_features(facts).reshape(1, -1)
        # One row: OpenMP thread overhead costs more than the prediction (hgb median 20 ms -> 4 ms).
        with self._threads.limit(limits=1):
            logits = self.model.decision_function(x)
            probs = probabilities(np.atleast_2d(logits), float(self.meta["temperature"]))[0]
            best = int(probs.argmax())
            shap_values = self.contributions(x, best)
        level = int(self.meta["classes"][best])
        label = display_label(level, scale.value)
        top = np.argsort(-np.abs(shap_values), kind="stable")[:3]
        fact_ids = feature_fact_ids(facts)
        reasons = []
        for i in top:
            feature = FEATURE_NAMES[int(i)]
            direction = "more" if shap_values[i] > 0 else "less"
            ktas = display_label(level, "KTAS")
            text = f"{describe(feature, float(x[0, i]))} made {ktas} {direction} likely"
            reasons.append({"text": text, "fact_ids": fact_ids[feature]})
        digest = hashlib.sha256(
            f"{facts.session_id}|{self.model_version}|{x.tobytes().hex()}".encode()
        ).hexdigest()
        return UrgencySuggestion.model_validate(
            {
                "suggestion_id": f"sug_{digest[:24]}",
                "session_id": facts.session_id,
                "level": level,
                "scale": scale.value,
                "display_label": label,
                "confidence": float(probs[best]),
                "reasons": reasons,
                "model_id": self.meta["model_id"],
                "model_version": self.model_version,
                "created_at": (now or datetime.now(UTC)).isoformat(),
            }
        )
