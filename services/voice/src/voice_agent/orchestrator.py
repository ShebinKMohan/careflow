"""Streaming LLM orchestrator — the production "brain" the voice agent calls per turn.

Contract the agent speaks (VOICE_AGENT_ORCHESTRATOR_URL points here):
    POST /voice/respond  {call_id, input_text, conversation_history?, system_prompt?,
                          provider?, model?}
      -> text/event-stream:  data: {"delta": "..."}\n\n ... data: [DONE]\n\n

It calls each provider's native streaming API over a reused pooled HTTP connection, so
the first token returns in ~0.3-0.8s and the agent starts speaking almost immediately.
Provider + model are chosen per request from the caller's agent-config. The agent sends
a bounded same-call user/assistant history so follow-up turns have context without
growing unbounded prompt cost.

Providers (explicit per-request `provider` wins; else FAST_LLM_PROVIDER env; else inferred):
    anthropic  -> Anthropic Messages API (default; claude-haiku-4-5)   ANTHROPIC_API_KEY
    openai     -> OpenAI Chat Completions (streaming)                  OPENAI_API_KEY
    google     -> Gemini via Google's OpenAI-compatible endpoint       GEMINI_API_KEY

Run (production):  uvicorn voice_agent.orchestrator:app --host 0.0.0.0 --port 8091
Then point the agent at it: VOICE_AGENT_ORCHESTRATOR_URL=http://<host>:8091/voice/respond
Auth: set ORCHESTRATOR_API_KEY here to the SAME secret as the agent's
VOICE_AGENT_ORCHESTRATOR_API_KEY; /voice/respond then requires `Authorization: Bearer
<key>` (401 otherwise). Unset only for loopback-only local dev.

On any provider error the turn yields no text and ends with [DONE], so the agent plays
its fallback line instead of speaking an error string to the caller.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from collections.abc import AsyncIterator
from typing import Any, Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DEFAULT_PERSONA = (
    "You are a warm, natural-sounding phone voice assistant for an Indian business, speaking "
    "Indian English. The user's message is a live speech-to-text transcript, so expect recognition "
    "errors and code-mixing."
)
# Always appended to the project's persona so voice-formatting + low-latency first-sentence
# behaviour holds no matter what prompt a project configures.
VOICE_RULES = (
    "\n\nVOICE OUTPUT RULES (these always win): keep your WHOLE reply to one or two short spoken "
    "sentences. If the answer will take more than a few words, start with a concrete "
    "3-to-6-word action phrase ending in a comma, then continue immediately. The first "
    "phrase must use the user's actual task or object, such as 'Dental cleaning, Monday "
    "morning,' or 'Account balance check,' not a generic acknowledgement. Do not start "
    "with filler or generic service phrases like 'Sure,' 'Okay,' 'Of course,' 'I'd be "
    "happy to,' 'Happy to help,' or 'I can help with that'; answer directly instead. "
    "Make the FIRST speakable phrase very short so the caller hears useful AI speech "
    "the instant you start. Speak for the ear, not the screen: use contractions; NEVER "
    "use markdown, bullet points, numbered lists, asterisks, or headings; read numbers, "
    "money, and dates the way they are spoken. The caller is on a live phone call "
    "waiting in real time. Only when the project prompt explicitly asks for a downstream "
    "workflow action, append one hidden directive after the spoken sentence using exactly "
    "[[ACTION:{\"type\":\"workflow.name\",\"payload\":{}}]]. Never speak, explain, or "
    "describe hidden directives to the caller."
)
MAX_TOKENS = int(os.environ.get("FAST_LLM_MAX_TOKENS", "300"))
MAX_INPUT_CHARS = int(os.environ.get("FAST_LLM_MAX_INPUT_CHARS", "2000"))
MAX_HISTORY_MESSAGES = int(os.environ.get("FAST_LLM_MAX_HISTORY_MESSAGES", "12"))

SUPPORTED_PROVIDERS = ("anthropic", "openai", "google")


def _provider_for(model: str | None) -> str:
    explicit = os.environ.get("FAST_LLM_PROVIDER", "").strip().lower()
    if explicit:
        return explicit
    name = (model or "").lower()
    if name.startswith("claude") or "haiku" in name or "sonnet" in name or "opus" in name:
        return "anthropic"
    if "gemini" in name:
        return "google"
    if name.startswith("gpt") or name.startswith("o1") or name.startswith("o3"):
        return "openai"
    return "anthropic"


# One reused async client — connection pooling + keep-alive eliminate the per-turn TLS
# handshake. Created lazily so import never fails without httpx extras.
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0))
    return _client


app = FastAPI(title="Voice LLM orchestrator", version="1.0.0")


def _require_orchestrator_auth(authorization: str | None = Header(default=None)) -> None:
    """Guard /voice/respond with a shared bearer secret. When ORCHESTRATOR_API_KEY is set
    (production), every request must present a constant-time-matching `Authorization:
    Bearer <key>` or get 401 — otherwise anyone who can reach the endpoint burns the
    operator's LLM API keys. When unset (local dev), the endpoint is open. The agent sends
    this as VOICE_AGENT_ORCHESTRATOR_API_KEY; set the SAME secret here as ORCHESTRATOR_API_KEY."""
    expected = os.environ.get("ORCHESTRATOR_API_KEY", "").strip()
    if not expected:
        return
    supplied = ""
    if authorization and authorization.startswith("Bearer "):
        supplied = authorization[len("Bearer ") :].strip()
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Unauthorized.")


class ConversationHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=MAX_INPUT_CHARS)


class RespondRequest(BaseModel):
    call_id: str = Field(..., min_length=1, max_length=128)
    input_text: str = Field(..., min_length=1)
    system_prompt: str | None = None
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    conversation_history: list[ConversationHistoryMessage] = Field(
        default_factory=list,
        max_length=MAX_HISTORY_MESSAGES,
    )


@app.get("/healthz")
async def healthz() -> dict[str, object]:
    keys = {
        "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "openai": bool(os.environ.get("OPENAI_API_KEY")),
        "google": bool(os.environ.get("GEMINI_API_KEY")),
    }
    provider = _provider_for(None)
    default_model = os.environ.get("FAST_LLM_MODEL", "").strip() or _default_model(provider)
    return {
        "status": "ok",
        "default_provider": provider,
        "default_model": default_model,
        "keys_present": keys,
    }


@app.post("/voice/respond")
async def voice_respond(
    req: RespondRequest, _: None = Depends(_require_orchestrator_auth)
) -> StreamingResponse:
    user_text = req.input_text.strip()[:MAX_INPUT_CHARS]
    if not user_text:
        raise HTTPException(status_code=400, detail="input_text must not be blank.")
    # Project persona (or a sane default) + voice rules that ALWAYS apply, so a project's
    # own prompt can never strip the short-first-sentence + no-markdown behaviour.
    persona = (req.system_prompt or "").strip() or DEFAULT_PERSONA
    system_prompt = persona + VOICE_RULES
    # Explicit per-request provider wins (the project's choice); else env default; else inferred.
    provider = (req.provider or "").strip().lower() or _provider_for(req.model)
    if provider not in SUPPORTED_PROVIDERS:
        provider = "anthropic"
    # Per-request model wins; else a deployment default (FAST_LLM_MODEL); else provider default.
    model = req.model or os.environ.get("FAST_LLM_MODEL", "").strip() or _default_model(provider)
    conversation_history = tuple(
        {"role": message.role, "content": message.content.strip()[:MAX_INPUT_CHARS]}
        for message in req.conversation_history
        if message.content.strip()
    )
    return StreamingResponse(
        _stream(
            provider,
            model,
            system_prompt,
            user_text,
            req.temperature,
            conversation_history,
        ),
        media_type="text/event-stream",
    )


def _default_model(provider: str) -> str:
    return {
        "anthropic": "claude-haiku-4-5",
        "openai": "gpt-4o-mini",
        "google": "gemini-2.5-flash",
    }.get(provider, "claude-haiku-4-5")


def _sse(delta: str) -> str:
    return "data: " + json.dumps({"delta": delta}) + "\n\n"


async def _stream(
    provider: str,
    model: str,
    system_prompt: str,
    user_text: str,
    temperature: float | None = None,
    conversation_history: tuple[dict[str, str], ...] = (),
) -> AsyncIterator[str]:
    try:
        if provider == "anthropic":
            stream = _anthropic(
                model,
                system_prompt,
                user_text,
                temperature,
                conversation_history,
            )
        else:
            stream = _openai_compatible(
                provider,
                model,
                system_prompt,
                user_text,
                temperature,
                conversation_history,
            )
        async for piece in stream:
            yield _sse(piece)
    except Exception:
        # Never speak a provider error to the caller: log it and end the turn cleanly so the
        # agent falls back to its safe reply line.
        logger.exception("Orchestrator turn failed (provider=%s model=%s).", provider, model)
    yield "data: [DONE]\n\n"


async def _anthropic(
    model: str,
    system_prompt: str,
    user_text: str,
    temperature: float | None = None,
    conversation_history: tuple[dict[str, str], ...] = (),
) -> AsyncIterator[str]:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        # prompt caching on the (stable) system prompt -> cheaper + faster on repeat turns
        "system": [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
        "messages": [
            *_conversation_messages(conversation_history),
            {"role": "user", "content": user_text},
        ],
        "stream": True,
    }
    if temperature is not None:
        body["temperature"] = temperature
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    async with _get_client().stream(
        "POST", "https://api.anthropic.com/v1/messages", json=body, headers=headers
    ) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta" and delta.get("text"):
                    yield delta["text"]


_OPENAI_BASE = {
    "openai": "https://api.openai.com/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai",
}
_OPENAI_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "google": "GEMINI_API_KEY",
}


def _openai_base_url(provider: str) -> str:
    # FAST_LLM_OPENAI_BASE_URL points the "openai" provider at any OpenAI-compatible
    # endpoint (Groq, Cerebras, vLLM, Ollama, ...). Low-TTFT hosts cut voice-to-voice
    # latency far more than any pipeline tuning: the LLM's first token is the single
    # largest stage in a cascaded turn.
    if provider == "openai":
        override = os.environ.get("FAST_LLM_OPENAI_BASE_URL", "").strip()
        if override:
            return override.rstrip("/")
    return _OPENAI_BASE[provider]


async def _openai_compatible(
    provider: str,
    model: str,
    system_prompt: str,
    user_text: str,
    temperature: float | None = None,
    conversation_history: tuple[dict[str, str], ...] = (),
) -> AsyncIterator[str]:
    key = os.environ.get(_OPENAI_KEY_ENV[provider])
    if not key:
        raise RuntimeError(f"{_OPENAI_KEY_ENV[provider]} not set")
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "stream": True,
        "messages": [
            {"role": "system", "content": system_prompt},
            *_conversation_messages(conversation_history),
            {"role": "user", "content": user_text},
        ],
    }
    if temperature is not None:
        body["temperature"] = temperature
    headers = {"authorization": f"Bearer {key}", "content-type": "application/json"}
    async with _get_client().stream(
        "POST", f"{_openai_base_url(provider)}/chat/completions", json=body, headers=headers
    ) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            choices = event.get("choices") or []
            if choices:
                delta = choices[0].get("delta", {}).get("content")
                if delta:
                    yield delta


def _conversation_messages(
    conversation_history: tuple[dict[str, str], ...],
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for message in conversation_history[-MAX_HISTORY_MESSAGES:]:
        role = message.get("role")
        content = (message.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        messages.append({"role": role, "content": content[:MAX_INPUT_CHARS]})
    return messages
