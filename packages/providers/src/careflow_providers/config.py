"""`config/providers.yaml`: which provider serves each AI task.

One line per task. Moving `extraction` from local Qwen to an API model is a one-line change:
`extraction: { provider: api, vendor: anthropic, model: <model> }`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

TaskName = Literal[
    "conversation", "extraction", "summary", "eval_judge", "test_data", "translation_check"
]
"""The tasks known today; later issues add more (the file may list any task name)."""

LIVE_TASKS: Final[frozenset[str]] = frozenset({"conversation", "extraction", "summary"})
"""Tasks on a live patient path: a subscription CLI may never serve them (ADR-004)."""

DEFAULT_LOCAL_BASE_URL: Final = "http://localhost:8081/v1"


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LocalTaskConfig(_Base):
    """An OpenAI-compatible local server: mlx_lm.server (:8081), Ollama (:11434) or LM Studio."""

    provider: Literal["local"]
    model: str
    """Logical model name (reports, cassette keys), e.g. qwen3.5-9b-4bit."""
    base_url: str = DEFAULT_LOCAL_BASE_URL
    served_model: str = "default_model"
    """Model id sent to the server. mlx_lm.server maps `default_model` to the model it was
    started with (scripts/with_llm.sh); Ollama and LM Studio need their own model tag."""


class ApiTaskConfig(_Base):
    """A hosted API through LangChain's init_chat_model. Off unless the owner adds a key."""

    provider: Literal["api"]
    vendor: Literal["anthropic", "openai", "gemini", "openrouter"]
    model: str


class CliTaskConfig(_Base):
    """A subscription CLI (Claude Code or Codex). Offline jobs only."""

    provider: Literal["cli"]
    tool: Literal["claude", "codex"]


TaskConfig = Annotated[
    LocalTaskConfig | ApiTaskConfig | CliTaskConfig, Field(discriminator="provider")
]


class ProvidersConfig(_Base):
    tasks: dict[str, TaskConfig]
    speech: dict[str, Any] | None = None  # STT/TTS routing, read by the voice service (M1-14)


class ConfigError(ValueError):
    """config/providers.yaml is malformed or breaks a rule."""


def parse_config(data: Any) -> ProvidersConfig:
    try:
        config = ProvidersConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid providers config: {exc}") from exc
    for task, task_config in config.tasks.items():
        if task in LIVE_TASKS and isinstance(task_config, CliTaskConfig):
            raise ConfigError(
                f"task {task!r} is on the live path; a subscription CLI may not serve it (ADR-004)"
            )
    return config


def load_config(path: str | Path) -> ProvidersConfig:
    return parse_config(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def describe(task_config: LocalTaskConfig | ApiTaskConfig | CliTaskConfig) -> str:
    match task_config:
        case LocalTaskConfig(model=model, base_url=base_url):
            return f"{model} @ {base_url}"
        case ApiTaskConfig(vendor=vendor, model=model):
            return f"{vendor}:{model}"
        case CliTaskConfig(tool=tool):
            return tool
