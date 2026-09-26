"""structured(): validate, repair once, then fail safe. Bad model output never raises."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence

from careflow_contracts import StructuredFacts
from careflow_contracts.testing import load_fixture
from careflow_providers import ChatResult, Message, StructuredFailure, StructuredOk, structured
from careflow_providers.base import MAX_ERROR_CHARS, extract_json, strip_thinking

FACTS = json.dumps(load_fixture("valid/chest_pain.structured-facts.json"))


class ScriptedProvider:
    """Replies with the scripted texts in order and records what it was sent."""

    task = "extraction"
    model_name = "scripted"

    def __init__(self, *replies: str) -> None:
        self._replies = list(replies)
        self.calls: list[list[Message]] = []

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        self.calls.append(list(messages))
        return ChatResult(text=self._replies.pop(0), model=self.model_name)

    async def stream(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        yield (await self.chat(messages)).text


USER: list[Message] = [{"role": "user", "content": "I have crushing chest pain since 20 minutes"}]


async def test_valid_first_time() -> None:
    result = await structured(ScriptedProvider(FACTS), USER, StructuredFacts)
    assert isinstance(result, StructuredOk)
    assert result.attempts == 1
    assert result.value.session_id == "ses_chest_pain"


async def test_bad_json_then_repair_then_ok() -> None:
    provider = ScriptedProvider('{"session_id": "x"', FACTS)
    result = await structured(provider, USER, StructuredFacts)
    assert isinstance(result, StructuredOk)
    assert result.attempts == 2
    repair = provider.calls[1][-1]
    assert repair["role"] == "user" and "did not validate" in repair["content"]


async def test_the_errors_sent_with_the_retry_are_cut_to_2000_characters() -> None:
    many_errors = json.dumps({"session_id": 1, "vitals": {f"x{i}": i for i in range(300)}})
    provider = ScriptedProvider(many_errors, FACTS)
    result = await structured(provider, USER, StructuredFacts)
    assert isinstance(result, StructuredOk) and result.attempts == 2
    repair = provider.calls[1][-1]["content"]
    errors = repair.split("Validation errors:\n", 1)[1]
    assert len(errors) == MAX_ERROR_CHARS == 2000


async def test_bad_json_twice_is_a_structured_failure() -> None:
    result = await structured(ScriptedProvider("not json", "still not json"), USER, StructuredFacts)
    assert isinstance(result, StructuredFailure)
    assert result.task == "extraction"
    assert len(result.errors) == 2
    assert result.raw_text == "still not json"


async def test_schema_is_in_the_system_prompt() -> None:
    provider = ScriptedProvider(FACTS)
    await structured(provider, USER, StructuredFacts)
    system = provider.calls[0][0]
    assert system["role"] == "system" and '"StructuredFacts"' in system["content"]


def test_thinking_blocks_and_fences_are_stripped() -> None:
    assert strip_thinking("<think>hmm\nok</think>\nanswer") == "answer"
    assert strip_thinking("<think>never closed") == ""
    wrapped = '<think>plan</think>Here you go:\n```json\n{"a": 1}\n```\nthanks'
    assert extract_json(wrapped) == '{"a": 1}'
    assert extract_json('prefix {"a": {"b": 2}} suffix {"c": 3}') == '{"a": {"b": 2}}'
