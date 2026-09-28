"""`build_provider(task, config)`: one `match` on the provider kind (after plivo-voice-agent's
`_build_llm`).

With `CAREFLOW_PROVIDER_MODE=record` or `replay` (and `CAREFLOW_CASSETTE=<file>`), every provider
is wrapped in the one `ReplayProvider`: `record` calls the configured provider and saves each
reply, `replay` answers only from the cassette and raises `CassetteMiss` for anything else, never
calling a model. Deterministic API tests and E2E runs use it (M1-12, M1-19); the live path leaves
it unset.
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from careflow_providers.adapters.api import ApiProvider, build_chat_model
from careflow_providers.adapters.cli import CliProvider
from careflow_providers.adapters.local import LocalProvider
from careflow_providers.adapters.replay import ReplayProvider, provider_mode
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
    """The provider configured for `task`, recorded or replayed when `CAREFLOW_PROVIDER_MODE`
    says so. `chat_model` injects a fake LangChain model (tests)."""
    mode = provider_mode()
    if mode == "replay":  # never builds the real provider: no key, server or CLI is needed
        return ReplayProvider(None, task=task, model=_model_name(task, config), mode=mode)
    provider = _configured(task, config, chat_model=chat_model)
    if mode in ("record", "add"):
        return ReplayProvider(provider, task=task, model=provider.model_name, mode=mode)
    return provider


def _model_name(task: str, config: ProvidersConfig) -> str:
    """The name the configured provider would report (its model, or the CLI tool)."""
    match config.tasks.get(task):
        case LocalTaskConfig(model=model) | ApiTaskConfig(model=model):
            return model
        case CliTaskConfig(tool=tool):
            return tool
        case _:
            raise ProviderUnavailable(task, "task is not configured in providers.yaml")


def _configured(
    task: str, config: ProvidersConfig, *, chat_model: BaseChatModel | None = None
) -> ChatProvider:
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
