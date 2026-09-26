"""ReplayProvider: record, replay and CassetteMiss."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest

from careflow_providers import CassetteMiss, ChatResult, Message, ReplayProvider
from careflow_providers.adapters.replay import cassette_key

MESSAGES: list[Message] = [{"role": "user", "content": "hello"}]


class Echo:
    task = "conversation"
    model_name = "echo-1"

    def __init__(self) -> None:
        self.calls = 0

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        self.calls += 1
        return ChatResult(text=f"echo: {messages[-1]['content']}", model=self.model_name)

    async def stream(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        yield (await self.chat(messages)).text


async def test_record_then_replay(tmp_path: Path) -> None:
    cassette = tmp_path / "cassette.json"
    inner = Echo()
    recorder = ReplayProvider(
        inner, task="conversation", model="echo-1", cassette=cassette, mode="record"
    )
    assert (await recorder.chat(MESSAGES)).text == "echo: hello"
    saved = json.loads(cassette.read_text())
    assert list(saved) == [cassette_key("conversation", "echo-1", MESSAGES, 0.0)]

    player = ReplayProvider(
        None, task="conversation", model="echo-1", cassette=cassette, mode="replay"
    )
    assert (await player.chat(MESSAGES)).text == "echo: hello"
    assert inner.calls == 1  # replay never calls a model


async def test_replay_miss_raises(tmp_path: Path) -> None:
    player = ReplayProvider(
        None, task="conversation", model="echo-1", cassette=tmp_path / "c.json", mode="replay"
    )
    with pytest.raises(CassetteMiss):
        await player.chat([{"role": "user", "content": "never recorded"}])


async def test_mode_and_cassette_come_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CAREFLOW_PROVIDER_MODE", "record")
    monkeypatch.setenv("CAREFLOW_CASSETTE", str(tmp_path / "env.json"))
    provider = ReplayProvider(Echo(), task="conversation", model="echo-1")
    assert provider.mode == "record"
    await provider.chat(MESSAGES)
    assert (tmp_path / "env.json").exists()


def test_key_is_canonical() -> None:
    a = cassette_key("t", "m", [{"role": "user", "content": "x"}], 0.0)
    b = cassette_key("t", "m", [{"content": "x", "role": "user"}], 0.0)
    assert a == b
    assert a != cassette_key("t", "m", [{"role": "user", "content": "x"}], 0.7)
