"""Tests for the shipped LLM orchestrator (voice_agent.orchestrator) — provider routing,
explicit-provider precedence, default models, and error-not-spoken behaviour."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from voice_agent import orchestrator


def test_provider_inference(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FAST_LLM_PROVIDER", raising=False)
    assert orchestrator._provider_for("claude-opus-4-8") == "anthropic"
    assert orchestrator._provider_for("gemini-2.5-flash") == "google"
    assert orchestrator._provider_for("gpt-4o") == "openai"
    assert orchestrator._provider_for("something-unknown") == "anthropic"


def test_explicit_env_provider_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAST_LLM_PROVIDER", "openai")
    assert orchestrator._provider_for("claude-opus-4-8") == "openai"


def test_default_model() -> None:
    assert orchestrator._default_model("anthropic") == "claude-haiku-4-5"
    assert orchestrator._default_model("openai") == "gpt-4o-mini"
    assert orchestrator._default_model("google") == "gemini-2.5-flash"
    assert orchestrator._default_model("weird") == "claude-haiku-4-5"


@pytest.mark.anyio
async def test_stream_swallows_provider_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    # No key -> _anthropic raises -> _stream logs and ends the turn with only [DONE],
    # never an error string the agent would speak to the caller.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("FAST_LLM_PROVIDER", raising=False)
    chunks = [
        chunk
        async for chunk in orchestrator._stream("anthropic", "claude-haiku-4-5", "sys", "hi")
    ]
    assert chunks == ["data: [DONE]\n\n"]
    assert all("error" not in chunk.lower() for chunk in chunks)


@pytest.mark.anyio
async def test_healthz_reports_per_provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("FAST_LLM_PROVIDER", raising=False)
    out = await orchestrator.healthz()
    assert out["keys_present"] == {"anthropic": True, "openai": False, "google": False}


def test_voice_respond_honors_explicit_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_stream(
        provider: str,
        model: str,
        system_prompt: str,
        user_text: str,
        temperature: float | None = None,
        conversation_history: tuple[dict[str, str], ...] = (),
    ) -> AsyncIterator[str]:
        captured["provider"] = provider
        captured["model"] = model
        captured["conversation_history"] = conversation_history
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(orchestrator, "_stream", fake_stream)
    monkeypatch.delenv("FAST_LLM_PROVIDER", raising=False)
    client = TestClient(orchestrator.app)

    response = client.post(
        "/voice/respond",
        json={"call_id": "c", "input_text": "hi", "provider": "openai", "model": "claude-opus-4-8"},
    )

    assert response.status_code == 200
    assert response.text == "data: [DONE]\n\n"
    # Explicit provider wins even when the model name would infer anthropic.
    assert captured["provider"] == "openai"
    assert captured["model"] == "claude-opus-4-8"
    assert captured["conversation_history"] == ()


def test_voice_respond_forwards_conversation_history(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_stream(
        provider: str,
        model: str,
        system_prompt: str,
        user_text: str,
        temperature: float | None = None,
        conversation_history: tuple[dict[str, str], ...] = (),
    ) -> AsyncIterator[str]:
        del provider, model, system_prompt, temperature
        captured["user_text"] = user_text
        captured["conversation_history"] = conversation_history
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(orchestrator, "_stream", fake_stream)
    monkeypatch.delenv("FAST_LLM_PROVIDER", raising=False)
    client = TestClient(orchestrator.app)

    response = client.post(
        "/voice/respond",
        json={
            "call_id": "c",
            "input_text": "second question",
            "conversation_history": [
                {"role": "user", "content": "first question"},
                {"role": "assistant", "content": "first answer"},
            ],
        },
    )

    assert response.status_code == 200
    assert captured["user_text"] == "second question"
    assert captured["conversation_history"] == (
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
    )


def test_voice_respond_appends_direct_substantive_opening_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_stream(
        provider: str,
        model: str,
        system_prompt: str,
        user_text: str,
        temperature: float | None = None,
        conversation_history: tuple[dict[str, str], ...] = (),
    ) -> AsyncIterator[str]:
        del provider, model, user_text, temperature, conversation_history
        captured["system_prompt"] = system_prompt
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(orchestrator, "_stream", fake_stream)
    monkeypatch.delenv("FAST_LLM_PROVIDER", raising=False)
    client = TestClient(orchestrator.app)

    response = client.post("/voice/respond", json={"call_id": "c", "input_text": "hi"})

    assert response.status_code == 200
    system_prompt = str(captured["system_prompt"])
    assert "concrete 3-to-6-word action phrase ending in a comma" in system_prompt
    assert "Dental cleaning, Monday morning," in system_prompt
    assert (
        "Do not start with filler or generic service phrases like "
        "'Sure,' 'Okay,' 'Of course,' 'I'd be happy to,'"
    ) in system_prompt
    assert "'I can help with that'" in system_prompt
    assert "Only when the project prompt explicitly asks for a downstream workflow action" in (
        system_prompt
    )
    assert '[[ACTION:{"type":"workflow.name","payload":{}}]]' in system_prompt
    assert "Never speak, explain, or describe hidden directives to the caller" in system_prompt


@pytest.mark.anyio
async def test_openai_compatible_includes_conversation_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_body: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        async def aiter_lines(self) -> AsyncIterator[str]:
            yield 'data: {"choices":[{"delta":{"content":"ok"}}]}'
            yield "data: [DONE]"

    class FakeStream:
        async def __aenter__(self) -> FakeResponse:
            return FakeResponse()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class FakeClient:
        def stream(
            self,
            method: str,
            url: str,
            *,
            json: dict[str, object],
            headers: dict[str, str],
        ) -> FakeStream:
            del method, url, headers
            captured_body.update(json)
            return FakeStream()

    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setattr(orchestrator, "_client", FakeClient())

    chunks = [
        chunk
        async for chunk in orchestrator._openai_compatible(
            "openai",
            "gpt-4o-mini",
            "system prompt",
            "second question",
            conversation_history=(
                {"role": "user", "content": "first question"},
                {"role": "assistant", "content": "first answer"},
            ),
        )
    ]

    assert chunks == ["ok"]
    assert captured_body["messages"] == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "second question"},
    ]


async def _stream_done(*_args: object, **_kwargs: object) -> AsyncIterator[str]:
    yield "data: [DONE]\n\n"


def test_voice_respond_requires_bearer_when_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_API_KEY", "shh-secret")
    monkeypatch.setattr(orchestrator, "_stream", _stream_done)
    monkeypatch.delenv("FAST_LLM_PROVIDER", raising=False)
    client = TestClient(orchestrator.app)
    body = {"call_id": "c", "input_text": "hi"}

    # No bearer -> 401, and the handler/stream never runs.
    assert client.post("/voice/respond", json=body).status_code == 401
    # Wrong bearer -> 401.
    assert (
        client.post(
            "/voice/respond", json=body, headers={"Authorization": "Bearer nope"}
        ).status_code
        == 401
    )
    # Correct bearer -> 200.
    ok = client.post(
        "/voice/respond", json=body, headers={"Authorization": "Bearer shh-secret"}
    )
    assert ok.status_code == 200
    assert ok.text == "data: [DONE]\n\n"


def test_voice_respond_open_when_key_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCHESTRATOR_API_KEY", raising=False)
    monkeypatch.setattr(orchestrator, "_stream", _stream_done)
    monkeypatch.delenv("FAST_LLM_PROVIDER", raising=False)
    client = TestClient(orchestrator.app)

    # With no key configured (local dev), the endpoint is open.
    resp = client.post("/voice/respond", json={"call_id": "c", "input_text": "hi"})
    assert resp.status_code == 200


def test_voice_respond_rejects_unsupported_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_stream(
        provider: str,
        model: str,
        system_prompt: str,
        user_text: str,
        temperature: float | None = None,
        conversation_history: tuple[dict[str, str], ...] = (),
    ) -> AsyncIterator[str]:
        del model, system_prompt, user_text, temperature
        captured["provider"] = provider
        captured["conversation_history"] = conversation_history
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(orchestrator, "_stream", fake_stream)
    monkeypatch.delenv("FAST_LLM_PROVIDER", raising=False)
    client = TestClient(orchestrator.app)

    response = client.post(
        "/voice/respond",
        json={"call_id": "c", "input_text": "hi", "provider": "groq"},
    )

    assert response.status_code == 200
    # groq is no longer supported -> falls back to anthropic rather than erroring.
    assert captured["provider"] == "anthropic"
    assert captured["conversation_history"] == ()
