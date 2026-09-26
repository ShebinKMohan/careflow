"""config/providers.yaml: the six tasks, the CLI rule and the one-line switch to an API model."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from careflow_providers import (
    ApiTaskConfig,
    ConfigError,
    LocalTaskConfig,
    ProviderUnavailable,
    build_provider,
    load_config,
    parse_config,
)
from careflow_providers.adapters.api import ApiProvider
from careflow_providers.adapters.local import LocalProvider

CONFIG = Path(__file__).resolve().parents[3] / "config" / "providers.yaml"
TASKS = ["conversation", "extraction", "summary", "eval_judge", "test_data", "translation_check"]


def test_the_repo_config_has_the_six_tasks() -> None:
    config = load_config(CONFIG)
    assert list(config.tasks) == TASKS
    assert isinstance(config.tasks["extraction"], LocalTaskConfig)
    assert config.tasks["extraction"].base_url == "http://localhost:8081/v1"
    assert config.speech is not None


def switched_extraction_line(text: str) -> str:
    """Edit only the extraction line, as a person would."""
    lines = text.splitlines()
    index = next(i for i, line in enumerate(lines) if line.strip().startswith("extraction:"))
    lines[index] = "  extraction:   { provider: api, vendor: anthropic, model: claude-sonnet-5 }"
    return "\n".join(lines)


def test_editing_only_the_extraction_line_switches_it_to_the_api_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = load_config(CONFIG)
    edited = tmp_path / "providers.yaml"
    edited.write_text(switched_extraction_line(CONFIG.read_text(encoding="utf-8")))
    after = load_config(edited)
    assert isinstance(after.tasks["extraction"], ApiTaskConfig)
    changed = [t for t in TASKS if before.tasks[t] != after.tasks[t]]
    assert changed == ["extraction"]
    assert isinstance(build_provider("conversation", after), LocalProvider)

    fake = GenericFakeChatModel(messages=iter([AIMessage(content="ok")]))
    assert isinstance(build_provider("extraction", after, chat_model=fake), ApiProvider)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder-test-value")  # never used on the network
    provider = build_provider("extraction", after)
    assert isinstance(provider, ApiProvider)
    assert provider.vendor == "anthropic"


def test_api_task_without_a_key_raises_provider_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config = parse_config(
        {"tasks": {"summary": {"provider": "api", "vendor": "anthropic", "model": "any"}}}
    )
    with pytest.raises(ProviderUnavailable) as info:
        build_provider("summary", config)
    assert info.value.task == "summary"
    assert "ANTHROPIC_API_KEY" in info.value.reason


@pytest.mark.parametrize("task", ["conversation", "extraction", "summary"])
def test_a_cli_may_not_serve_a_live_task(task: str) -> None:
    with pytest.raises(ConfigError, match="live path"):
        parse_config({"tasks": {task: {"provider": "cli", "tool": "codex"}}})


def test_unknown_fields_and_vendors_are_rejected() -> None:
    with pytest.raises(ConfigError):
        parse_config({"tasks": {"x": {"provider": "api", "vendor": "litellm", "model": "m"}}})
    with pytest.raises(ConfigError):
        parse_config({"tasks": {"x": {"provider": "local", "model": "m", "temperature": 1}}})


def test_unconfigured_task_is_unavailable() -> None:
    with pytest.raises(ProviderUnavailable):
        build_provider("zero_shot", load_config(CONFIG))


def test_cli_tasks_are_marked_offline_in_yaml() -> None:
    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert {t for t, c in raw["tasks"].items() if c["provider"] == "cli"} == {
        "eval_judge",
        "test_data",
    }
