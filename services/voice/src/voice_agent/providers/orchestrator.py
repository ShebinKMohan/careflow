from __future__ import annotations

import asyncio
import json
import re
import time
from codecs import getincrementaldecoder
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from voice_agent.providers._retry import stream_with_retry


class OrchestratorClientError(RuntimeError):
    """Raised when the custom orchestration service returns an invalid response."""


DEFAULT_MAX_RESPONSE_BYTES = 64 * 1024
DEFAULT_FIRST_CHUNK_TIMEOUT_SECONDS = 5.0
TURN_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
MAX_CONVERSATION_HISTORY_MESSAGES = 12
MAX_TRACKED_CALL_HISTORIES = 256


class OrchestratorLLMClient:
    def __init__(
        self,
        *,
        url: str,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 20.0,
        first_chunk_timeout_seconds: float = DEFAULT_FIRST_CHUNK_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        require_streaming_response: bool = False,
        system_prompt: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        retry_max_attempts: int = 1,
        retry_backoff_seconds: float = 0.25,
        retry_max_backoff_seconds: float = 2.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        if first_chunk_timeout_seconds <= 0:
            raise ValueError("first_chunk_timeout_seconds must be positive.")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive.")
        if retry_max_attempts < 1:
            raise ValueError("retry_max_attempts must be at least 1.")
        self._system_prompt = (system_prompt or "").strip() or None
        self._provider = (provider or "").strip().lower() or None
        self._model = (model or "").strip() or None
        self._temperature = temperature
        self._url = _required_endpoint_url(url, "url")
        self._api_key = (
            _required_config_string(api_key, "api_key") if api_key is not None else None
        )
        self._timeout_seconds = timeout_seconds
        self._first_chunk_timeout_seconds = first_chunk_timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._require_streaming_response = require_streaming_response
        self._retry_max_attempts = retry_max_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._retry_max_backoff_seconds = retry_max_backoff_seconds
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None
        self._conversation_history_by_call_id: dict[str, list[dict[str, str]]] = {}

    async def start(self) -> None:
        headers: dict[str, str] = {}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
        await self._client.get(_healthz_url_for_endpoint(self._url), headers=headers)

    async def generate_reply(
        self,
        *,
        call_id: str,
        user_text: str,
        commit_history: bool = True,
    ) -> AsyncIterator[str]:
        clean_call_id = _required_turn_identifier(call_id, "call_id")
        clean_user_text = _required_turn_string(user_text, "user_text")
        headers = {"accept": "text/event-stream, application/json"}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"

        body: dict[str, Any] = {"call_id": clean_call_id, "input_text": clean_user_text}
        conversation_history = self._conversation_history_by_call_id.get(clean_call_id)
        if conversation_history:
            body["conversation_history"] = [dict(message) for message in conversation_history]
        if self._system_prompt:
            body["system_prompt"] = self._system_prompt
        if self._provider:
            body["provider"] = self._provider
        if self._model:
            body["model"] = self._model
        if self._temperature is not None:
            body["temperature"] = self._temperature

        async with stream_with_retry(
            self._client,
            "POST",
            self._url,
            headers=headers,
            json=body,
            max_attempts=self._retry_max_attempts,
            backoff_seconds=self._retry_backoff_seconds,
            max_backoff_seconds=self._retry_max_backoff_seconds,
        ) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if _is_sse_content_type(content_type):
                reply_chunks: list[str] = []
                async for chunk in _iter_sse_text(
                    response,
                    max_response_bytes=self._max_response_bytes,
                    first_chunk_timeout_seconds=self._first_chunk_timeout_seconds,
                ):
                    reply_chunks.append(chunk)
                    yield chunk
                if commit_history:
                    self._remember_turn(clean_call_id, clean_user_text, "".join(reply_chunks))
                return
            if self._require_streaming_response:
                raise OrchestratorClientError(
                    "Orchestrator response must use text/event-stream when streaming "
                    "is required."
                )

            payload = await _read_limited_response_body(
                response,
                max_response_bytes=self._max_response_bytes,
                first_chunk_timeout_seconds=self._first_chunk_timeout_seconds,
            )
            data = _decode_orchestrator_json(payload)
            text = _extract_text(data)
            if not text:
                raise OrchestratorClientError("Orchestrator response did not include reply text.")
            yield text
            if commit_history:
                self._remember_turn(clean_call_id, clean_user_text, text)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def remember_turn(self, *, call_id: str, user_text: str, assistant_text: str) -> None:
        clean_call_id = _required_turn_identifier(call_id, "call_id")
        clean_user_text = _required_turn_string(user_text, "user_text")
        self._remember_turn(clean_call_id, clean_user_text, assistant_text)

    def _remember_turn(self, call_id: str, user_text: str, assistant_text: str) -> None:
        clean_assistant_text = assistant_text.strip()
        if not clean_assistant_text:
            return
        if (
            call_id not in self._conversation_history_by_call_id
            and len(self._conversation_history_by_call_id) >= MAX_TRACKED_CALL_HISTORIES
        ):
            oldest_call_id = next(iter(self._conversation_history_by_call_id))
            self._conversation_history_by_call_id.pop(oldest_call_id, None)
        history = self._conversation_history_by_call_id.setdefault(call_id, [])
        history.extend(
            [
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": clean_assistant_text},
            ]
        )
        if len(history) > MAX_CONVERSATION_HISTORY_MESSAGES:
            del history[: len(history) - MAX_CONVERSATION_HISTORY_MESSAGES]


def _is_sse_content_type(content_type: str) -> bool:
    media_type = content_type.split(";", maxsplit=1)[0].strip().lower()
    return media_type == "text/event-stream"


def _healthz_url_for_endpoint(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, "/healthz", "", ""))


async def _iter_sse_text(
    response: httpx.Response,
    *,
    max_response_bytes: int,
    first_chunk_timeout_seconds: float,
) -> AsyncIterator[str]:
    _reject_oversized_content_length(response, max_response_bytes=max_response_bytes)
    yielded_text = False
    data_lines: list[str] = []
    total_response_bytes = 0
    decoder = getincrementaldecoder("utf-8")()
    buffered_text = ""
    first_chunk_deadline_at = time.perf_counter() + first_chunk_timeout_seconds
    byte_iterator = response.aiter_bytes().__aiter__()

    while True:
        try:
            chunk = await _read_next_response_chunk(
                byte_iterator,
                first_chunk_deadline_at=(
                    None if yielded_text else first_chunk_deadline_at
                ),
            )
        except StopAsyncIteration:
            break
        if not chunk:
            continue
        total_response_bytes += len(chunk)
        if total_response_bytes > max_response_bytes:
            raise _response_too_large()
        try:
            buffered_text += decoder.decode(chunk)
        except UnicodeDecodeError as exc:
            raise OrchestratorClientError(
                "Orchestrator SSE response was not valid UTF-8."
            ) from exc
        while True:
            line_end = buffered_text.find("\n")
            if line_end < 0:
                break
            line = buffered_text[:line_end]
            buffered_text = buffered_text[line_end + 1 :]
            done, text = _consume_sse_line(line, data_lines)
            if text:
                yielded_text = True
                yield text
            if done:
                if not yielded_text:
                    raise OrchestratorClientError(
                        "Orchestrator response did not include reply text."
                    )
                return

    try:
        buffered_text += decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise OrchestratorClientError(
            "Orchestrator SSE response was not valid UTF-8."
        ) from exc
    if buffered_text:
        done, text = _consume_sse_line(buffered_text, data_lines)
        if text:
            yielded_text = True
            yield text
        if done:
            if not yielded_text:
                raise OrchestratorClientError(
                    "Orchestrator response did not include reply text."
                )
            return
    if data_lines:
        _done, text = _parse_sse_event(data_lines)
        if text:
            yielded_text = True
            yield text
    if not yielded_text:
        raise OrchestratorClientError("Orchestrator response did not include reply text.")


def _consume_sse_line(data_line: str, data_lines: list[str]) -> tuple[bool, str | None]:
    line = data_line[:-1] if data_line.endswith("\r") else data_line
    if not line:
        done, text = _parse_sse_event(data_lines)
        data_lines.clear()
        return done, text
    if line.startswith(":"):
        return False, None
    if not line.startswith("data:"):
        return False, None
    data_lines.append(line.removeprefix("data:").lstrip())
    return False, None


async def _read_limited_response_body(
    response: httpx.Response,
    *,
    max_response_bytes: int,
    first_chunk_timeout_seconds: float,
) -> bytes:
    _reject_oversized_content_length(response, max_response_bytes=max_response_bytes)
    chunks: list[bytes] = []
    total_response_bytes = 0
    reply_deadline_at = time.perf_counter() + first_chunk_timeout_seconds
    byte_iterator = response.aiter_bytes().__aiter__()
    while True:
        try:
            chunk = await _read_next_response_chunk(
                byte_iterator,
                first_chunk_deadline_at=reply_deadline_at,
            )
        except StopAsyncIteration:
            break
        if not chunk:
            continue
        total_response_bytes += len(chunk)
        if total_response_bytes > max_response_bytes:
            raise _response_too_large()
        chunks.append(chunk)
    return b"".join(chunks)


async def _read_next_response_chunk(
    byte_iterator: AsyncIterator[bytes],
    *,
    first_chunk_deadline_at: float | None,
) -> bytes:
    if first_chunk_deadline_at is None:
        return await anext(byte_iterator)
    timeout_seconds = first_chunk_deadline_at - time.perf_counter()
    if timeout_seconds <= 0:
        raise _first_chunk_timeout()
    try:
        return await asyncio.wait_for(anext(byte_iterator), timeout=timeout_seconds)
    except TimeoutError as exc:
        raise _first_chunk_timeout() from exc


def _reject_oversized_content_length(
    response: httpx.Response,
    *,
    max_response_bytes: int,
) -> None:
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            body_size = int(content_length)
        except ValueError as exc:
            raise OrchestratorClientError(
                "Orchestrator response Content-Length was not an integer."
            ) from exc
        if body_size <= 0:
            raise OrchestratorClientError(
                "Orchestrator response Content-Length must be positive."
            )
        if body_size > max_response_bytes:
            raise _response_too_large()


def _response_too_large() -> OrchestratorClientError:
    return OrchestratorClientError(
        "Orchestrator response exceeded configured size limit."
    )


def _first_chunk_timeout() -> OrchestratorClientError:
    return OrchestratorClientError("Orchestrator first reply chunk timed out.")


def _parse_sse_event(data_lines: list[str]) -> tuple[bool, str | None]:
    if not data_lines:
        return False, None
    payload = "\n".join(data_lines).strip()
    if payload == "[DONE]":
        return True, None
    data = _decode_orchestrator_json(payload)
    return False, _extract_text(data)


def _decode_orchestrator_json(payload: str | bytes) -> Any:
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise OrchestratorClientError("Orchestrator response was not valid JSON.") from exc


def _extract_text(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    for key in ("delta", "text", "reply", "content"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value
    choices = data.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            text = _extract_choice_text(choice)
            if text is not None:
                return text
    return None


def _extract_choice_text(choice: Any) -> str | None:
    if not isinstance(choice, dict):
        return None
    for key in ("delta", "message"):
        text = _extract_nested_content(choice.get(key))
        if text is not None:
            return text
    return _extract_nested_content(choice)


def _extract_nested_content(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, dict):
        content = value.get("content")
        if isinstance(content, str) and content.strip():
            return content
    return None


def _required_config_string(value: str, name: str) -> str:
    clean_value = value.strip()
    if not clean_value:
        raise ValueError(f"{name} must not be blank.")
    return clean_value


def _required_endpoint_url(value: str, name: str) -> str:
    clean_value = _required_config_string(value, name).rstrip("/")
    parsed_url = urlsplit(clean_value)
    if parsed_url.scheme not in {"http", "https"}:
        raise ValueError(f"{name} must use http or https.")
    if parsed_url.hostname is None:
        raise ValueError(f"{name} must be a valid http or https URL with a host.")
    if parsed_url.username is not None or parsed_url.password is not None:
        raise ValueError(f"{name} must not include credentials.")
    if parsed_url.query or parsed_url.fragment:
        raise ValueError(f"{name} must not include a query string or fragment.")
    return clean_value


def _required_turn_string(value: str, name: str) -> str:
    clean_value = value.strip()
    if not clean_value:
        raise OrchestratorClientError(f"{name} must not be blank.")
    return clean_value


def _required_turn_identifier(value: str, name: str) -> str:
    clean_value = _required_turn_string(value, name)
    if TURN_IDENTIFIER_RE.fullmatch(clean_value) is None:
        raise OrchestratorClientError(
            f"{name} must be 1-128 characters using letters, numbers, dot, "
            "underscore, colon, or hyphen."
        )
    return clean_value
