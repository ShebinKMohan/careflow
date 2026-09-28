"""Model selection and failure behavior, without a database or loading model weights."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from careflow_api.intake import wiring
from careflow_api.intake.ports import NoTriageModel, TriageUrgency
from careflow_triage.inference import UrgencyModel
from careflow_triage.laya_ktas.model import LayaUrgencyModel


def checkpoint(path: Path, filename: str) -> Path:
    path.mkdir(parents=True)
    (path / filename).touch()
    return path


def test_laya_selection_never_falls_back_to_existing_hgb(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wiring, "ARTIFACTS", tmp_path)
    monkeypatch.delenv("TRIAGE_MODEL_PATH", raising=False)
    hgb = checkpoint(tmp_path / "ktas-hgb-test", "model.joblib")
    monkeypatch.setenv("TRIAGE_MODEL_KIND", "laya")
    assert wiring.triage_model_path() == tmp_path / "none"
    laya = checkpoint(tmp_path / "laya" / "laya-ktas-test", "model.safetensors")
    assert wiring.triage_model_path() == laya
    monkeypatch.setenv("TRIAGE_MODEL_KIND", "hgb")
    assert wiring.triage_model_path() == hgb


def test_explicit_model_path_is_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRIAGE_MODEL_PATH", str(tmp_path))
    assert wiring.triage_model_path() == tmp_path


def test_laya_loader_uses_selected_device_threads_and_real_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "model.safetensors").touch()
    (tmp_path / "rl_agent_config.json").write_text(
        json.dumps(
            {
                "fine_tuned": True,
                "model_name": "laya-ktas",
                "temperature": [1.6],
                "calibration": {"fitted_on": "val"},
            }
        )
    )
    calls = []
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(set_num_threads=calls.append))
    loaded = SimpleNamespace(model_id="laya-ktas", model_version="laya-test")

    def load(path: Path, *, device: str) -> SimpleNamespace:
        assert path == tmp_path and device == "cpu"
        return loaded

    monkeypatch.setattr(LayaUrgencyModel, "load", load)
    result = TriageUrgency.load(tmp_path, kind="laya", device="cpu", threads=2)
    assert isinstance(result, TriageUrgency)
    assert (result.model_id, result.model_version) == ("laya-ktas", "laya-test")
    assert calls == [2]


@pytest.mark.parametrize(
    "config",
    [
        {"fine_tuned": False, "temperature": [1.6]},
        {"fine_tuned": True, "temperature": [0]},
        {"fine_tuned": True, "temperature": [1.6]},
    ],
)
def test_unverified_laya_artifact_returns_diagnostic_not_hgb(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: dict,
) -> None:
    (tmp_path / "rl_agent_config.json").write_text(json.dumps(config))

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("The HGB loader must never replace selected Laya")

    monkeypatch.setattr(UrgencyModel, "load", forbidden)
    result = TriageUrgency.load(tmp_path, kind="laya")
    assert isinstance(result, NoTriageModel)
    assert "Selected laya" in result.reason
    assert result.model_version == "none"


def test_broken_selected_artifact_keeps_nurse_review_available(tmp_path: Path) -> None:
    result = TriageUrgency.load(tmp_path, kind="hgb")
    assert isinstance(result, NoTriageModel)
    assert "Selected hgb" in result.reason
