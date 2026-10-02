"""Gemini REST adapter; the same ChatProvider contract and original output gates.

No model weights or Gemini SDK response-version assumptions in the hosted runtime. Keys are
headers on server-to-Google requests, never URLs or browser configuration. Quota errors surface
to the caller's existing fail-safe path; no paid-model fallback or automatic quota retry.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from careflow_providers.base import ChatResult, Message
from careflow_providers.errors import ProviderUnavailable

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


def api_key(task: str) -> str:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise ProviderUnavailable(task, "GEMINI_API_KEY is not set")
    return key


class GeminiProvider:
    def __init__(self, task: str, model: str) -> None:
        if not re.fullmatch(r"gemini-[a-zA-Z0-9.-]+", model):
            raise ValueError("Invalid Gemini model id")
        self.task, self.model_name = task, model
        self._key = api_key(task)

    def _body(self, messages: Sequence[Message], max_tokens: int | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "contents": [
                {
                    "role": "model" if m["role"] == "assistant" else "user",
                    "parts": [{"text": m["content"]}],
                }
                for m in messages
                if m["role"] != "system"
            ],
            "generationConfig": {"maxOutputTokens": max_tokens or 4096},
        }
        instructions = [m["content"] for m in messages if m["role"] == "system"]
        if instructions:
            body["systemInstruction"] = {"parts": [{"text": "\n\n".join(instructions)}]}
        return body

    def _check(self, response: httpx.Response) -> None:
        if response.is_error:
            # Google's body may echo input; do not carry it into application logs.
            raise ProviderUnavailable(self.task, f"Gemini HTTP {response.status_code}")

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.post(
                f"{BASE_URL}/models/{self.model_name}:generateContent",
                headers={"x-goog-api-key": self._key},
                json=self._body(messages, max_tokens),
            )
        self._check(response)
        body = response.json()
        candidates = body.get("candidates") or []
        parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text:
            raise ProviderUnavailable(self.task, "Gemini returned no usable text")
        usage = body.get("usageMetadata") or {}
        return ChatResult(
            text=text,
            model=self.model_name,
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
        )

    async def stream(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        async with (
            httpx.AsyncClient(timeout=25) as client,
            client.stream(
                "POST",
                f"{BASE_URL}/models/{self.model_name}:streamGenerateContent?alt=sse",
                headers={"x-goog-api-key": self._key},
                json=self._body(messages),
            ) as response,
        ):
            self._check(response)
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                body = json.loads(line[6:])
                for candidate in body.get("candidates", []):
                    for part in candidate.get("content", {}).get("parts", []):
                        if part.get("text") and not part.get("thought"):
                            yield part["text"]
