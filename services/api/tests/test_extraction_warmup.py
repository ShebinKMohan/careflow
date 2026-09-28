"""Startup verifies real extraction contracts without mutating patient deadlines or records."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence

import pytest

from careflow_api.intake.warmup import warm_local_extraction
from careflow_intake.extraction.extractor import LlmExtractor
from careflow_providers.base import ChatResult, Message


class Provider:
    task = "extraction"
    model_name = "test-local"

    def __init__(self, *, invalid: bool = False, slow: bool = False) -> None:
        self.invalid = invalid
        self.slow = slow
        self.calls: list[Sequence[Message]] = []

    async def chat(
        self, messages: Sequence[Message], *, temperature: float = 0, max_tokens: int | None = None
    ) -> ChatResult:
        self.calls.append(messages)
        if self.slow:
            await asyncio.sleep(2)
        text = messages[-1]["content"].split("]: ", 1)[-1]
        facts = (
            []
            if self.invalid
            else [{"key": "chief_complaint", "value": text, "quote": text, "turn_id": "t1"}]
        )
        return ChatResult(text=json.dumps({"facts": facts}), model=self.model_name)

    async def stream(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        yield "unused"


async def test_warmup_verifies_both_actual_prompts_and_preserves_patient_timeout() -> None:
    provider = Provider()
    extractor = LlmExtractor(provider, timeout_seconds=20, provider_kind="local")
    rows = await warm_local_extraction(extractor)
    assert [row["language"] for row in rows] == ["en", "ar"]
    assert all(row["verified_facts"] == 1 for row in rows)
    assert all("JSON Schema" in call[0]["content"] for call in provider.calls)
    assert extractor.timeout_seconds == 20
    assert len(provider.calls) == 2


async def test_empty_model_response_is_not_declared_ready() -> None:
    with pytest.raises(RuntimeError, match="no verified chief complaint"):
        await warm_local_extraction(LlmExtractor(Provider(invalid=True), provider_kind="local"))


async def test_startup_work_has_one_bounded_deadline() -> None:
    with pytest.raises(RuntimeError, match="did not finish"):
        await warm_local_extraction(
            LlmExtractor(Provider(slow=True), provider_kind="local"), timeout_seconds=0.01
        )


async def test_replay_and_other_extractors_are_never_warmed() -> None:
    provider = Provider()
    assert await warm_local_extraction(LlmExtractor(provider, provider_kind="replay")) == []
    assert await warm_local_extraction(object()) == []
    assert not provider.calls
