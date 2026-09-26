"""Training on the synthetic fixture (NOT A RESULT) proves the pipeline.

Inference follows the M1-05 port.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from careflow_contracts.generated.structured_facts_schema import AgeMonthsFact
from careflow_contracts.generated.urgency_suggestion_schema import TriageScale, UrgencySuggestion
from careflow_triage.inference import OutOfScopeError, UrgencyModel
from careflow_triage.ktas.features import facts_to_features, row_to_facts
from careflow_triage.ktas.load import load_ktas
from careflow_triage.report import write_model_card, write_reports
from careflow_triage.train import train

NOW = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)


def test_fixture_artifacts_are_labelled_and_complete(trained) -> None:  # noqa: ANN001
    for result in (trained.lr, trained.hgb):
        meta = result.meta
        assert result.model_version.startswith("fixture-ktas-") and meta["fixture"] is True
        assert (result.artifact_dir / "model.joblib").exists() and (
            result.artifact_dir / "meta.json"
        ).exists()
        assert {
            "features",
            "temperature",
            "libraries",
            "csv_sha256",
            "split_file",
            "metrics",
        } <= set(meta)
        assert 0.05 <= meta["temperature"] <= 10
        test = meta["metrics"]["test"]
        assert {
            "under_triage_l1_2",
            "over_triage",
            "macro_f1",
            "ece_before",
            "ece_after",
            "confusion",
        } <= set(test)
        assert meta["metrics"]["nurse_comparator_test"]["n"] == test["n"]
    assert len(trained.hgb.meta["grid"]) == 4 and trained.lr.meta["grid"] == []


def test_same_seed_same_metrics(fixture_csv: Path, trained, tmp_path: Path) -> None:  # noqa: ANN001
    again = train("hgb", fixture_csv, artifacts_dir=tmp_path, splits_path=trained.splits)
    assert again.meta["metrics"] == trained.hgb.meta["metrics"]
    assert again.meta["temperature"] == trained.hgb.meta["temperature"]


def test_reports_say_not_a_result(trained, tmp_path: Path) -> None:  # noqa: ANN001
    sha, seed = trained.lr.meta["csv_sha256"], trained.lr.meta["seed"]
    json_path, md_path = write_reports(
        csv_sha256=sha, seed=seed, artifacts_dir=trained.artifacts, reports_dir=tmp_path
    )
    assert md_path.name.startswith("fixture_ktas_baseline_")
    text = md_path.read_text(encoding="utf-8")
    assert (
        "NOT A RESULT" in text
        and "| hgb `" in text
        and "| lr `" in text
        and "nurses (`KTAS_RN`)" in text
    )
    assert "Under-triage L1-2" in text and "ECE before" in text
    assert set(json.loads(json_path.read_text(encoding="utf-8"))["models"]) == {"hgb", "lr"}
    card = write_model_card(
        csv_sha256=sha, seed=seed, artifacts_dir=trained.artifacts, path=tmp_path / "CARD.md"
    )
    assert "NOT A RESULT" in card.read_text(encoding="utf-8")


@pytest.mark.parametrize("kind", ["lr", "hgb"])
def test_predict_returns_three_reasons_fast(trained, fixture_csv: Path, kind: str) -> None:  # noqa: ANN001
    model = UrgencyModel.load(getattr(trained, kind).artifact_dir)
    rows = load_ktas(fixture_csv).to_dict("records")
    facts = [row_to_facts(r, index=i) for i, r in enumerate(rows)]
    for f in facts[:5]:
        model.predict(f)  # warm-up
    timings = []
    for f in facts:
        start = time.perf_counter()
        suggestion = model.predict(f, now=NOW)
        timings.append(time.perf_counter() - start)
        assert isinstance(suggestion, UrgencySuggestion)
        assert len(suggestion.reasons) == 3 and suggestion.model_version == model.model_version
        own_ids = {fid for r in suggestion.reasons for fid in r.fact_ids}
        assert own_ids <= {
            fact_id for fact_id in f.model_dump_json().split('"') if fact_id.startswith("f_")
        }
    assert max(timings) < 0.020, f"slowest predict {max(timings) * 1000:.1f} ms"


def test_display_labels_follow_the_scale(trained, fixture_csv: Path) -> None:  # noqa: ANN001
    model = UrgencyModel.load(trained.lr.artifact_dir)
    facts = row_to_facts(load_ktas(fixture_csv).to_dict("records")[0])
    labels = {
        scale: model.predict(facts, scale=scale, now=NOW).display_label for scale in TriageScale
    }
    level = model.predict(facts, now=NOW).level
    assert labels[TriageScale.KTAS] == f"KTAS {level}" and labels[TriageScale.ESI] == f"ESI {level}"
    assert labels[TriageScale.CTAS] in {
        "Resuscitation",
        "Emergent",
        "Urgent",
        "Less urgent",
        "Non-urgent",
    }
    assert labels[TriageScale.MTS] in {"Red", "Orange", "Yellow", "Green", "Blue"}


def test_children_get_no_suggestion(trained, fixture_csv: Path) -> None:  # noqa: ANN001
    """The KTAS data are adults (15 and over): a known younger age raises instead of suggesting."""
    model = UrgencyModel.load(trained.hgb.artifact_dir)
    row = load_ktas(fixture_csv).to_dict("records")[0]
    assert model.predict(row_to_facts({**row, "Age": 15}), now=NOW).level in {1, 2, 3, 4, 5}
    with pytest.raises(OutOfScopeError, match="adults only"):
        model.predict(row_to_facts({**row, "Age": 14}), now=NOW)
    adult = row_to_facts({**row, "Age": 40})
    assert adult.patient is not None and adult.patient.age_years is not None
    months = AgeMonthsFact.model_validate({**adult.patient.age_years.model_dump(), "value": 179})
    child = adult.model_copy(
        update={"patient": adult.patient.model_copy(update={"age_months": months})}
    )
    with pytest.raises(OutOfScopeError):
        model.predict(child, now=NOW)
    no_age = adult.model_copy(
        update={"patient": adult.patient.model_copy(update={"age_years": None})}
    )
    assert model.predict(no_age, now=NOW).reasons


@pytest.mark.parametrize("kind", ["lr", "hgb"])
def test_shap_values_add_up_to_each_class_logit(trained, fixture_csv: Path, kind: str) -> None:  # noqa: ANN001
    """The reasons are faithful: per class, the SHAP values plus the base value equal the logit."""
    model = UrgencyModel.load(getattr(trained, kind).artifact_dir)
    for i, row in enumerate(load_ktas(fixture_csv).to_dict("records")[:10]):
        x = facts_to_features(row_to_facts(row, index=i)).reshape(1, -1)
        logits = np.asarray(model.model.decision_function(x))[0]
        base = np.atleast_1d(model._explainer.expected_value)  # noqa: SLF001
        for k in range(len(model.meta["classes"])):
            assert model.contributions(x, k).sum() + base[k] == pytest.approx(logits[k], abs=1e-6)
