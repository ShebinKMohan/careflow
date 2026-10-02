"""Gemini protocol, credential handling and failure behavior without calling Google."""

import json

import httpx
import pytest
import respx

from careflow_providers.adapters.gemini import BASE_URL, GeminiProvider
from careflow_providers.errors import ProviderUnavailable


@respx.mock
async def test_roles_key_and_visible_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-server-secret")
    route = respx.post(f"{BASE_URL}/models/gemini-test:generateContent").mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "private reasoning", "thought": True},
                                {"text": "approved output"},
                            ]
                        }
                    }
                ],
                "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 2},
            },
        )
    )
    result = await GeminiProvider("summary", "gemini-test").chat(
        [
            {"role": "system", "content": "follow the source gate"},
            {"role": "user", "content": "synthetic input"},
            {"role": "assistant", "content": "earlier answer"},
        ],
        max_tokens=120,
    )
    request = route.calls[0].request
    assert request.headers["x-goog-api-key"] == "test-server-secret"
    assert "test-server-secret" not in str(request.url)
    assert "test-server-secret" not in request.content.decode()
    body = json.loads(request.content)
    assert [c["role"] for c in body["contents"]] == ["user", "model"]
    assert body["systemInstruction"]["parts"][0]["text"] == "follow the source gate"
    assert body["generationConfig"]["maxOutputTokens"] == 120
    assert result.text == "approved output"
    assert result.input_tokens == 4


@pytest.mark.parametrize("status", [401, 429, 503])
@respx.mock
async def test_errors_do_not_retry_or_echo_input(
    status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-server-secret")
    route = respx.post(f"{BASE_URL}/models/gemini-test:generateContent").mock(
        return_value=httpx.Response(status, json={"error": "synthetic private input"})
    )
    with pytest.raises(ProviderUnavailable) as failure:
        await GeminiProvider("summary", "gemini-test").chat([{"role": "user", "content": "x"}])
    assert "synthetic private input" not in str(failure.value)
    assert route.call_count == 1


@respx.mock
async def test_blocked_reply_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-server-secret")
    respx.post(f"{BASE_URL}/models/gemini-test:generateContent").mock(
        return_value=httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})
    )
    with pytest.raises(ProviderUnavailable):
        await GeminiProvider("summary", "gemini-test").chat([{"role": "user", "content": "x"}])


@respx.mock
async def test_stream_discards_thought_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-server-secret")
    body = {
        "candidates": [
            {"content": {"parts": [{"text": "reasoning", "thought": True}, {"text": "visible"}]}}
        ]
    }
    respx.post(f"{BASE_URL}/models/gemini-test:streamGenerateContent").mock(
        return_value=httpx.Response(200, text="data: " + json.dumps(body) + "\n\n")
    )
    chunks = [
        c
        async for c in GeminiProvider("summary", "gemini-test").stream(
            [{"role": "user", "content": "x"}]
        )
    ]
    assert chunks == ["visible"]
