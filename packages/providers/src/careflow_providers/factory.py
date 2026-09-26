"""`build_provider(task, config)`: one `match` on the provider kind (after plivo-voice-agent's
`_build_llm`)."""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from careflow_providers.adapters.api import ApiProvider, build_chat_model
from careflow_providers.adapters.cli import CliProvider
from careflow_providers.adapters.local import LocalProvider
from careflow_providers.base import ChatProvider
from careflow_providers.config import (
    ApiTaskConfig,
    CliTaskConfig,
    LocalTaskConfig,
    ProvidersConfig,
)
from careflow_providers.errors import ProviderUnavailable


def build_provider(
    task: str, config: ProvidersConfig, *, chat_model: BaseChatModel | None = None
) -> ChatProvider:
    """The provider configured for `task`. `chat_model` injects a fake LangChain model (tests)."""
    task_config = config.tasks.get(task)
    if task_config is None:
        raise ProviderUnavailable(task, "task is not configured in providers.yaml")
    match task_config:
        case LocalTaskConfig(model=model, base_url=base_url, served_model=served_model):
            return LocalProvider(task, model, base_url, served_model=served_model)
        case ApiTaskConfig(vendor=vendor, model=model):
            model_impl = chat_model or build_chat_model(task, vendor, model)
            return ApiProvider(task, vendor, model, model_impl)
        case CliTaskConfig(tool=tool):
            return CliProvider(task, tool)
