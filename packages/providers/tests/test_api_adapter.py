"""The API adapter with LangChain's GenericFakeChatModel (no network, no keys)."""

from __future__ import annotations

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from careflow_providers.adapters.api import ApiProvider, _to_langchain


def fake(*replies: str) -> GenericFakeChatModel:
    return GenericFakeChatModel(messages=iter([AIMessage(content=r) for r in replies]))


async def test_chat_returns_the_model_reply() -> None:
    provider = ApiProvider("summary", "anthropic", "any-model", fake("A short summary."))
    result = await provider.chat([{"role": "user", "content": "summarise"}], temperature=0.2)
    assert result.text == "A short summary."
    assert result.model == "any-model"


async def test_stream_yields_chunks() -> None:
    provider = ApiProvider("summary", "openai", "any-model", fake("one two three"))
    chunks = [c async for c in provider.stream([{"role": "user", "content": "x"}])]
    assert "".join(chunks) == "one two three"
    assert len(chunks) > 1


def test_messages_map_to_langchain_roles() -> None:
    converted = _to_langchain(
        [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": "a"},
        ]
    )
    assert [m.type for m in converted] == ["system", "human", "ai"]
