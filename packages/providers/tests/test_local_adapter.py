"""The local OpenAI-compatible adapter, offline with respx."""

from __future__ import annotations

import json

import httpx
import openai
import pytest
import respx

from careflow_providers.adapters.local import LocalProvider

BASE = "http://localhost:8081/v1"
URL = f"{BASE}/chat/completions"


def completion(content: str) -> dict[str, object]:
    return {
        "id": "c1",
        "object": "chat.completion",
        "created": 0,
        "model": "default_model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
    }


async def no_sleep(_: float) -> None:
    return None


def provider(**kwargs: object) -> LocalProvider:
    return LocalProvider("extraction", "qwen3.5-9b-4bit", BASE, sleep=no_sleep, **kwargs)  # type: ignore[arg-type]


@respx.mock
async def test_chat_strips_thinking_and_disables_it_in_the_request() -> None:
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, json=completion("<think>x</think>hi"))
    )
    result = await provider().chat([{"role": "user", "content": "hello"}])
    assert result.text == "hi"
    assert (result.model, result.input_tokens, result.output_tokens) == ("qwen3.5-9b-4bit", 12, 4)
    sent = json.loads(route.calls[0].request.content)
    assert sent["model"] == "default_model"
    assert sent["chat_template_kwargs"] == {"enable_thinking": False}


@respx.mock
async def test_retries_503_then_succeeds() -> None:
    route = respx.post(URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=completion("ok"))]
    )
    assert (await provider().chat([{"role": "user", "content": "x"}])).text == "ok"
    assert route.call_count == 2


@respx.mock
async def test_honours_retry_after_on_429() -> None:
    delays: list[float] = []

    async def record(delay: float) -> None:
        delays.append(delay)

    respx.post(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "2"}),
            httpx.Response(200, json=completion("ok")),
        ]
    )
    local = LocalProvider("extraction", "m", BASE, sleep=record)
    await local.chat([{"role": "user", "content": "x"}])
    assert delays == [2.0]


@respx.mock
async def test_does_not_retry_a_client_error() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(400, json={"error": "bad"}))
    with pytest.raises(openai.BadRequestError):
        await provider().chat([{"role": "user", "content": "x"}])
    assert route.call_count == 1


@respx.mock
async def test_gives_up_after_max_attempts() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(502))
    with pytest.raises(openai.APIStatusError):
        await provider(max_attempts=3).chat([{"role": "user", "content": "x"}])
    assert route.call_count == 3


@respx.mock
async def test_stream_yields_deltas_without_thinking() -> None:
    chunks = ["<think>plan", " more</think>Hel", "lo", " there"]
    lines = [
        "data: "
        + json.dumps(
            {
                "id": "s",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "default_model",
                "choices": [{"index": 0, "delta": {"content": c}, "finish_reason": None}],
            }
        )
        for c in chunks
    ]
    body = "\n\n".join([*lines, "data: [DONE]"]) + "\n\n"
    respx.post(URL).mock(
        return_value=httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
    )
    out = [delta async for delta in provider().stream([{"role": "user", "content": "x"}])]
    assert "".join(out) == "Hello there"
