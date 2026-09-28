"""The local OpenAI-compatible adapter, offline with respx."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from typing import cast

import httpx
import openai
import pytest
import respx

from careflow_providers.adapters.local import LocalProvider

BASE = "http://localhost:8081/v1"
URL = f"{BASE}/chat/completions"


def chunk(content: str | None = None, *, usage: bool = False) -> bytes:
    payload = {
        "id": "c1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "default_model",
        "choices": []
        if usage
        else [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}
        if usage
        else None,
    }
    return f"data: {json.dumps(payload)}\n\n".encode()


def completion(*content: str, usage: bool = True) -> httpx.Response:
    data = b"".join(chunk(part) for part in content)
    if usage:
        data += chunk(usage=True)
    return httpx.Response(
        200,
        content=data + b"data: [DONE]\n\n",
        headers={"content-type": "text/event-stream"},
    )


class TrackedBody(httpx.AsyncByteStream):
    """A streaming HTTP body that can stall or fail after any number of chunks."""

    def __init__(
        self, chunks: list[bytes], *, block: bool = False, error: Exception | None = None
    ) -> None:
        self.chunks = chunks
        self.block = block
        self.error = error
        self.waiting = asyncio.Event()
        self.closed = asyncio.Event()
        self.close_count = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for data in self.chunks:
            yield data
        if self.block:
            self.waiting.set()
            await asyncio.Event().wait()
        if self.error:
            raise self.error

    async def aclose(self) -> None:
        self.close_count += 1
        self.closed.set()


def streaming_response(body: TrackedBody) -> httpx.Response:
    return httpx.Response(200, stream=body, headers={"content-type": "text/event-stream"})


async def no_sleep(_: float) -> None:
    return None


def provider(**kwargs: object) -> LocalProvider:
    return LocalProvider("extraction", "qwen3.5-9b-4bit", BASE, sleep=no_sleep, **kwargs)  # type: ignore[arg-type]


@respx.mock
async def test_chat_strips_thinking_and_disables_it_in_the_request() -> None:
    route = respx.post(URL).mock(return_value=completion("<thi", "nk>x</think>h", "i"))
    result = await provider().chat([{"role": "user", "content": "hello"}])
    assert result.text == "hi"
    assert (result.model, result.input_tokens, result.output_tokens) == ("qwen3.5-9b-4bit", 12, 4)
    sent = json.loads(route.calls[0].request.content)
    assert sent["model"] == "default_model"
    assert sent["chat_template_kwargs"] == {"enable_thinking": False}
    assert sent["stream"] is True
    assert sent["stream_options"] == {"include_usage": True}
    assert sent["temperature"] == 0.0
    assert "max_tokens" not in sent


@respx.mock
async def test_chat_preserves_settings_and_missing_usage() -> None:
    route = respx.post(URL).mock(return_value=completion("hello", usage=False))
    messages = [{"role": "user", "content": "hello"}]
    result = await provider(disable_thinking=False, served_model="served-model").chat(
        [{"role": "user", "content": "hello"}], temperature=0.25, max_tokens=321
    )
    assert result.text == "hello"
    assert (result.input_tokens, result.output_tokens) == (None, None)
    sent = json.loads(route.calls[0].request.content)
    assert sent["messages"] == messages
    assert sent["model"] == "served-model"
    assert sent["temperature"] == 0.25
    assert sent["max_tokens"] == 321
    assert "chat_template_kwargs" not in sent


@respx.mock
async def test_retries_503_then_succeeds() -> None:
    route = respx.post(URL).mock(side_effect=[httpx.Response(503), completion("ok")])
    assert (await provider().chat([{"role": "user", "content": "x"}])).text == "ok"
    assert route.call_count == 2


@respx.mock
async def test_retries_connection_failure_before_stream_establishment() -> None:
    route = respx.post(URL).mock(
        side_effect=[httpx.ConnectError("connection refused"), completion("ok")]
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
            completion("ok"),
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


@respx.mock
async def test_chat_closes_completed_stream() -> None:
    body = TrackedBody([chunk("complete"), chunk(usage=True), b"data: [DONE]\n\n"])
    respx.post(URL).mock(return_value=streaming_response(body))
    result = await provider().chat([{"role": "user", "content": "x"}])
    assert result.text == "complete"
    assert (result.input_tokens, result.output_tokens) == (12, 4)
    assert body.closed.is_set()
    assert body.close_count == 1


@pytest.mark.parametrize("partial", [False, True])
@respx.mock
async def test_chat_cancellation_closes_pending_stream_without_retry(partial: bool) -> None:
    body = TrackedBody([chunk("partial")] if partial else [], block=True)
    route = respx.post(URL).mock(return_value=streaming_response(body))
    task = asyncio.create_task(provider().chat([{"role": "user", "content": "x"}]))
    try:
        await asyncio.wait_for(body.waiting.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert body.closed.is_set()
        assert route.call_count == 1
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@respx.mock
async def test_chat_transport_error_closes_without_returning_or_retrying_partial() -> None:
    failure = httpx.ReadError("connection lost after a token")
    body = TrackedBody([chunk("partial")], error=failure)
    route = respx.post(URL).mock(return_value=streaming_response(body))
    with pytest.raises(httpx.ReadError) as caught:
        await provider().chat([{"role": "user", "content": "x"}])
    assert caught.value is failure
    assert body.closed.is_set()
    assert route.call_count == 1


@respx.mock
async def test_chat_server_stream_error_closes_without_retrying_partial() -> None:
    body = TrackedBody([chunk("partial"), b'data: {"error":{"message":"generation failed"}}\n\n'])
    route = respx.post(URL).mock(return_value=streaming_response(body))
    with pytest.raises(openai.APIError, match="generation failed"):
        await provider().chat([{"role": "user", "content": "x"}])
    assert body.closed.is_set()
    assert route.call_count == 1


@respx.mock
async def test_stream_consumer_exit_closes_unread_response() -> None:
    body = TrackedBody([chunk("first"), chunk("unread")], block=True)
    route = respx.post(URL).mock(return_value=streaming_response(body))
    output = cast(AsyncGenerator[str, None], provider().stream([{"role": "user", "content": "x"}]))
    assert await anext(output) == "first"
    await output.aclose()
    assert body.closed.is_set()
    assert body.close_count == 1
    assert route.call_count == 1


@respx.mock
async def test_stream_error_preserves_exception_and_closes_without_retry() -> None:
    failure = httpx.ReadError("stream interrupted")
    body = TrackedBody([chunk("first")], error=failure)
    route = respx.post(URL).mock(return_value=streaming_response(body))
    output = provider().stream([{"role": "user", "content": "x"}])
    assert await anext(output) == "first"
    with pytest.raises(httpx.ReadError) as caught:
        await anext(output)
    assert caught.value is failure
    assert body.closed.is_set()
    assert route.call_count == 1
