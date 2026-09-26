"""One async interface for every AI task: chat, streaming and validated structured output."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, TypedDict, runtime_checkable

from pydantic import BaseModel, ValidationError


class Message(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class ChatResult:
    text: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


@runtime_checkable
class ChatProvider(Protocol):
    """Implemented by the local, api, cli and replay adapters."""

    task: str
    model_name: str

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult: ...

    def stream(self, messages: Sequence[Message]) -> AsyncIterator[str]: ...


@dataclass(frozen=True, slots=True)
class StructuredOk[T: BaseModel]:
    value: T
    raw_text: str
    attempts: int


@dataclass(frozen=True, slots=True)
class StructuredFailure:
    """The model's output failed validation twice. The caller shows "extraction incomplete" and
    the rules engine still runs on whatever facts exist (fail safe)."""

    task: str
    errors: list[str] = field(default_factory=list)
    raw_text: str = ""


_THINK = re.compile(r"<think>.*?(</think>|$)", re.S | re.I)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def strip_thinking(text: str) -> str:
    """Remove `<think>...</think>` blocks (Qwen3.x reasoning), including an unterminated one."""
    return _THINK.sub("", text).strip()


def extract_json(text: str) -> str:
    """The first complete JSON object in `text` (after removing thinking and code fences).

    Returns the text unchanged when no object is found, so validation reports the real output.
    """
    cleaned = strip_thinking(text)
    fenced = _FENCE.search(cleaned)
    if fenced:
        cleaned = fenced.group(1).strip()
    decoder = json.JSONDecoder()
    for start, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            obj, end = decoder.raw_decode(cleaned[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return cleaned[start : start + end]
    return cleaned


def schema_instruction[T: BaseModel](schema: type[T]) -> str:
    compact = json.dumps(schema.model_json_schema(), separators=(",", ":"))
    return (
        "Reply with one JSON object only, no prose and no code fences. It must validate "
        f"against this JSON Schema:\n{compact}"
    )


async def structured[T: BaseModel](
    provider: ChatProvider,
    messages: Sequence[Message],
    schema: type[T],
    *,
    temperature: float = 0.0,
    max_tokens: int | None = None,
) -> StructuredOk[T] | StructuredFailure:
    """Chat, then validate against `schema`; on a validation error retry once with the error
    appended; after a second failure return `StructuredFailure`. Never raises for bad output."""
    # One system message at the start: several chat templates (Qwen's included) reject a second.
    instruction = schema_instruction(schema)
    if messages and messages[0]["role"] == "system":
        first: Message = {"role": "system", "content": f"{messages[0]['content']}\n\n{instruction}"}
        conversation: list[Message] = [first, *messages[1:]]
    else:
        conversation = [{"role": "system", "content": instruction}, *messages]
    errors: list[str] = []
    raw = ""
    for attempt in (1, 2):
        result = await provider.chat(conversation, temperature=temperature, max_tokens=max_tokens)
        raw = result.text
        try:
            value = schema.model_validate_json(extract_json(raw))
        except ValidationError as exc:
            errors.append(str(exc))
            conversation = [
                *conversation,
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": "That reply did not validate. Fix it and reply with the corrected "
                    f"JSON object only. Validation errors:\n{exc}",
                },
            ]
            continue
        return StructuredOk(value=value, raw_text=raw, attempts=attempt)
    return StructuredFailure(task=provider.task, errors=errors, raw_text=raw)
