"""Local OpenAI-compatible server: mlx_lm.server (:8081), Ollama (:11434/v1), LM Studio (:1234/v1).

`response_format` is not relied on (mlx_lm.server may ignore it): `structured()` puts the JSON
Schema in the system prompt, and the reply's `<think>` block is stripped before parsing. Qwen3.x
thinking is switched off per request through `chat_template_kwargs` (mlx_lm.server honours it;
other servers ignore it), so the stripping is only a backstop.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any

import openai

from careflow_providers._retry import RETRYABLE_STATUSES, compute_backoff, retry_after_seconds
from careflow_providers.base import ChatResult, Message, strip_thinking

SleepFn = Callable[[float], Awaitable[None]]


class LocalProvider:
    def __init__(
        self,
        task: str,
        model: str,
        base_url: str,
        *,
        client: openai.AsyncOpenAI | None = None,
        max_attempts: int = 3,
        backoff_seconds: float = 0.5,
        max_backoff_seconds: float = 8.0,
        timeout_seconds: float = 300.0,
        sleep: SleepFn = asyncio.sleep,
        disable_thinking: bool = True,
        served_model: str = "default_model",
    ) -> None:
        self.task = task
        self.model_name = model
        self.served_model = served_model
        self.base_url = base_url
        # The SDK's own retries are off: ours retry only before any output is streamed.
        self._client = client or openai.AsyncOpenAI(
            base_url=base_url, api_key="local", max_retries=0, timeout=timeout_seconds
        )
        self._max_attempts = max_attempts
        self._backoff = backoff_seconds
        self._max_backoff = max_backoff_seconds
        self._sleep = sleep
        self._extra_body: dict[str, Any] = (
            {"chat_template_kwargs": {"enable_thinking": False}} if disable_thinking else {}
        )

    async def _with_retry[R](self, call: Callable[[], Awaitable[R]]) -> R:
        attempt = 0
        while True:
            attempt += 1
            try:
                return await call()
            except openai.APIStatusError as exc:
                if attempt >= self._max_attempts or exc.status_code not in RETRYABLE_STATUSES:
                    raise
                delay = retry_after_seconds(exc.response, max_seconds=self._max_backoff)
                if delay is None:
                    delay = compute_backoff(
                        attempt, base_seconds=self._backoff, max_seconds=self._max_backoff
                    )
            except (openai.APIConnectionError, openai.APITimeoutError):
                if attempt >= self._max_attempts:
                    raise
                delay = compute_backoff(
                    attempt, base_seconds=self._backoff, max_seconds=self._max_backoff
                )
            await self._sleep(delay)

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        kwargs: dict[str, Any] = {"model": self.served_model, "messages": list(messages)}
        kwargs["temperature"] = temperature
        kwargs["extra_body"] = self._extra_body
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        response = await self._with_retry(lambda: self._client.chat.completions.create(**kwargs))
        choice = response.choices[0]
        usage = response.usage
        return ChatResult(
            text=strip_thinking(choice.message.content or ""),
            model=self.model_name,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
        )

    async def stream(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        """Yield content deltas without `<think>` blocks. Retries only before the first token."""
        kwargs: dict[str, Any] = {"model": self.served_model, "messages": list(messages)}
        kwargs |= {"stream": True, "temperature": 0.0, "extra_body": self._extra_body}
        stream = await self._with_retry(lambda: self._client.chat.completions.create(**kwargs))
        thinking = False
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content or ""
            if "<think>" in delta:
                thinking = True
                delta = delta.split("<think>", 1)[0]
            if thinking:
                if "</think>" not in delta:
                    continue
                thinking = False
                delta = delta.split("</think>", 1)[1]
            if delta:
                yield delta
