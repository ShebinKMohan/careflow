"""Hosted API providers through LangChain's `init_chat_model` (never LiteLLM, ADR-003).

Keys come from the environment only and are never logged. Without the vendor's key,
`build_provider` raises `ProviderUnavailable`; callers fall back to local/replay or mark not_run.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Sequence
from typing import Any, Final

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from careflow_providers.base import ChatResult, Message
from careflow_providers.errors import ProviderUnavailable

KEY_ENV: Final[dict[str, str]] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}
LANGCHAIN_PROVIDER: Final[dict[str, str]] = {
    "anthropic": "anthropic",
    "openai": "openai",
    "gemini": "google_genai",
    "openrouter": "openai",
}
OPENROUTER_BASE_URL: Final = "https://openrouter.ai/api/v1"


def _to_langchain(messages: Sequence[Message]) -> list[BaseMessage]:
    kinds = {"system": SystemMessage, "user": HumanMessage, "assistant": AIMessage}
    return [kinds[m["role"]](content=m["content"]) for m in messages]


def build_chat_model(task: str, vendor: str, model: str) -> BaseChatModel:
    key_env = KEY_ENV[vendor]
    api_key = os.environ.get(key_env)
    if vendor == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY") or api_key
    if not api_key:
        raise ProviderUnavailable(task, f"{key_env} is not set")
    from langchain.chat_models import init_chat_model  # heavy import, only when a key exists

    kwargs: dict[str, Any] = {"model_provider": LANGCHAIN_PROVIDER[vendor], "api_key": api_key}
    if vendor == "openrouter":
        kwargs["base_url"] = OPENROUTER_BASE_URL
    return init_chat_model(model, **kwargs)


class ApiProvider:
    def __init__(self, task: str, vendor: str, model: str, chat_model: BaseChatModel) -> None:
        self.task = task
        self.vendor = vendor
        self.model_name = model
        self._model = chat_model

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        bound = self._model.bind(temperature=temperature)
        if max_tokens is not None:
            bound = self._model.bind(temperature=temperature, max_tokens=max_tokens)
        reply = await bound.ainvoke(_to_langchain(messages))
        usage = getattr(reply, "usage_metadata", None) or {}
        return ChatResult(
            text=str(reply.content),
            model=self.model_name,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )

    async def stream(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        async for chunk in self._model.astream(_to_langchain(messages)):
            if chunk.content:
                yield str(chunk.content)

    async def structured_native[T: BaseModel](
        self, messages: Sequence[Message], schema: type[T]
    ) -> T:
        """Vendor-native structured output (`with_structured_output`)."""
        runnable = self._model.with_structured_output(schema)
        result = await runnable.ainvoke(_to_langchain(messages))
        return schema.model_validate(result)
