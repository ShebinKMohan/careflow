from __future__ import annotations

import pytest

from careflow_intake.wiring import providers_config
from careflow_providers.config import CliTaskConfig, LocalTaskConfig


@pytest.mark.parametrize(
    ("repo", "label"),
    [
        ("mlx-community/Qwen3.5-4B-4bit", "qwen3.5-4b-4bit"),
        ("mlx-community/Qwen3.5-9B-MLX-4bit", "qwen3.5-9b-4bit"),
    ],
)
def test_runtime_selection_changes_all_local_labels_only(
    monkeypatch: pytest.MonkeyPatch, repo: str, label: str
) -> None:
    monkeypatch.setenv("CAREFLOW_LLM_MODEL", repo)
    monkeypatch.setenv("CAREFLOW_LOCAL_LLM_BASE_URL", "http://127.0.0.1:8181/v1")
    config = providers_config()
    for task in ("conversation", "extraction", "summary", "translation_check"):
        selected = config.tasks[task]
        assert isinstance(selected, LocalTaskConfig)
        assert selected.model == label
        assert selected.base_url == "http://127.0.0.1:8181/v1"
    assert isinstance(config.tasks["eval_judge"], CliTaskConfig)


def test_no_runtime_selection_preserves_recorded_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAREFLOW_LLM_MODEL", raising=False)
    monkeypatch.delenv("CAREFLOW_LOCAL_LLM_BASE_URL", raising=False)
    selected = providers_config().tasks["extraction"]
    assert isinstance(selected, LocalTaskConfig)
    assert selected.model == "qwen3.5-9b-4bit"
