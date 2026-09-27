from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote, urlsplit

import httpx

E164_PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")
PLIVO_CALL_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
DEFAULT_MAX_RESPONSE_BYTES = 64 * 1024


class PlivoCallClientError(RuntimeError):
    """Raised when Plivo returns an invalid call-control response."""


@dataclass(frozen=True, slots=True)
class PlivoCallCreateResult:
    api_id: str
    message: str
    request_uuid: str


class OutboundCallClient(Protocol):
    async def create_call(
        self,
        *,
        from_number: str,
        to_number: str,
        answer_url: str,
        answer_method: str,
        hangup_url: str,
        hangup_method: str,
        hangup_on_ring_seconds: int,
        time_limit_seconds: int,
    ) -> PlivoCallCreateResult: ...

    async def hangup_call(self, identifier: str) -> None: ...


class PlivoOutboundCallClient:
    def __init__(
        self,
        *,
        auth_id: str,
        auth_token: str,
        api_base_url: str = "https://api.plivo.com",
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 10.0,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive.")
        self._auth_id = _required_config_string(auth_id, "auth_id")
        self._auth_token = _required_config_string(auth_token, "auth_token")
        self._api_base_url = _required_https_url(api_base_url, "api_base_url")
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def create_call(
        self,
        *,
        from_number: str,
        to_number: str,
        answer_url: str,
        answer_method: str,
        hangup_url: str,
        hangup_method: str,
        hangup_on_ring_seconds: int,
        time_limit_seconds: int,
    ) -> PlivoCallCreateResult:
        clean_from_number = _required_phone_number(from_number, "from_number")
        clean_to_number = _required_phone_number(to_number, "to_number")
        clean_answer_url = _required_callback_url(answer_url, "answer_url")
        clean_answer_method = _required_http_method(answer_method, "answer_method")
        clean_hangup_url = _required_callback_url(hangup_url, "hangup_url")
        clean_hangup_method = _required_http_method(hangup_method, "hangup_method")
        clean_hangup_on_ring_seconds = _required_positive_int(
            hangup_on_ring_seconds,
            "hangup_on_ring_seconds",
        )
        clean_time_limit_seconds = _required_positive_int(
            time_limit_seconds,
            "time_limit_seconds",
        )
        async with self._client.stream(
            "POST",
            self._create_call_url,
            auth=httpx.BasicAuth(self._auth_id, self._auth_token),
            headers={"accept": "application/json"},
            json={
                "from": clean_from_number,
                "to": clean_to_number,
                "answer_url": clean_answer_url,
                "answer_method": clean_answer_method,
                "hangup_url": clean_hangup_url,
                "hangup_method": clean_hangup_method,
                "hangup_on_ring": clean_hangup_on_ring_seconds,
                "time_limit": clean_time_limit_seconds,
            },
        ) as response:
            payload = await _read_limited_plivo_response(
                response,
                max_response_bytes=self._max_response_bytes,
                operation="create-call",
            )
            if not response.is_success:
                httpx.Response(
                    response.status_code,
                    headers=response.headers,
                    content=payload,
                    request=response.request,
                    extensions=response.extensions,
                ).raise_for_status()
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise PlivoCallClientError("Plivo create-call response was not valid JSON.") from exc
        return PlivoCallCreateResult(
            api_id=_required_string(data, "api_id"),
            message=_required_string(data, "message"),
            request_uuid=_required_response_identifier(data, "request_uuid"),
        )

    async def hangup_call(self, identifier: str) -> None:
        cleaned_identifier = _required_hangup_identifier(identifier)
        async with self._client.stream(
            "DELETE",
            self._call_url(cleaned_identifier),
            auth=httpx.BasicAuth(self._auth_id, self._auth_token),
            headers={"accept": "application/json"},
        ) as response:
            payload = await _read_limited_plivo_response(
                response,
                max_response_bytes=self._max_response_bytes,
                operation="hangup",
            )
            if not response.is_success:
                httpx.Response(
                    response.status_code,
                    headers=response.headers,
                    content=payload,
                    request=response.request,
                    extensions=response.extensions,
                ).raise_for_status()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @property
    def _create_call_url(self) -> str:
        escaped_auth_id = quote(self._auth_id, safe="")
        return f"{self._api_base_url}/v1/Account/{escaped_auth_id}/Call/"

    def _call_url(self, identifier: str) -> str:
        escaped_auth_id = quote(self._auth_id, safe="")
        escaped_identifier = quote(identifier, safe="")
        return f"{self._api_base_url}/v1/Account/{escaped_auth_id}/Call/{escaped_identifier}/"


def _required_string(data: object, key: str) -> str:
    if not isinstance(data, dict):
        raise PlivoCallClientError("Plivo create-call response was not a JSON object.")
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PlivoCallClientError(f"Plivo create-call response is missing {key}.")
    return value.strip()


def _required_response_identifier(data: object, key: str) -> str:
    value = _required_string(data, key)
    if PLIVO_CALL_IDENTIFIER_RE.fullmatch(value) is None:
        raise PlivoCallClientError(
            f"Plivo create-call response {key} must be 1-128 characters using letters, "
            "numbers, ., _, :, or -."
        )
    return value


async def _read_limited_plivo_response(
    response: httpx.Response,
    *,
    max_response_bytes: int,
    operation: str,
) -> bytes:
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            response_size = int(content_length)
        except ValueError as exc:
            raise PlivoCallClientError(
                f"Plivo {operation} response Content-Length was not an integer."
            ) from exc
        if response_size < 0:
            raise PlivoCallClientError(
                f"Plivo {operation} response Content-Length must be non-negative."
            )
        if response_size > max_response_bytes:
            raise _plivo_response_too_large(operation)

    chunks: list[bytes] = []
    total_response_bytes = 0
    async for chunk in response.aiter_bytes():
        if not chunk:
            continue
        total_response_bytes += len(chunk)
        if total_response_bytes > max_response_bytes:
            raise _plivo_response_too_large(operation)
        chunks.append(chunk)
    return b"".join(chunks)


def _plivo_response_too_large(operation: str) -> PlivoCallClientError:
    return PlivoCallClientError(f"Plivo {operation} response exceeds configured size limit.")


def _required_hangup_identifier(value: str) -> str:
    clean_value = value.strip()
    if not clean_value:
        raise PlivoCallClientError("Plivo hangup identifier is required.")
    if PLIVO_CALL_IDENTIFIER_RE.fullmatch(clean_value) is None:
        raise PlivoCallClientError(
            "Plivo hangup identifier must be 1-128 characters using letters, "
            "numbers, ., _, :, or -."
        )
    return clean_value


def _required_argument(value: str, name: str) -> str:
    clean_value = value.strip()
    if not clean_value:
        raise PlivoCallClientError(f"Plivo create-call argument {name} is required.")
    return clean_value


def _required_http_method(value: str, name: str) -> str:
    clean_value = _required_argument(value, name).upper()
    if clean_value not in {"GET", "POST"}:
        raise PlivoCallClientError(f"Plivo create-call argument {name} must be GET or POST.")
    return clean_value


def _required_phone_number(value: str, name: str) -> str:
    clean_value = _required_argument(value, name)
    if E164_PHONE_RE.fullmatch(clean_value) is None:
        raise PlivoCallClientError(f"Plivo create-call argument {name} must be an E.164 number.")
    return clean_value


def _required_callback_url(value: str, name: str) -> str:
    clean_value = _required_argument(value, name)
    parsed_url = urlsplit(clean_value)
    if parsed_url.scheme != "https":
        raise PlivoCallClientError(f"Plivo create-call argument {name} must be an https:// URL.")
    if parsed_url.hostname is None:
        raise PlivoCallClientError(
            f"Plivo create-call argument {name} must be a valid https URL with a host."
        )
    if parsed_url.username is not None or parsed_url.password is not None:
        raise PlivoCallClientError(
            f"Plivo create-call argument {name} must not include credentials."
        )
    if parsed_url.fragment:
        raise PlivoCallClientError(
            f"Plivo create-call argument {name} must not include a fragment."
        )
    return clean_value


def _required_positive_int(value: int, name: str) -> int:
    if value <= 0:
        raise PlivoCallClientError(f"Plivo create-call argument {name} must be positive.")
    return value


def _required_config_string(value: str, name: str) -> str:
    clean_value = value.strip()
    if not clean_value:
        raise ValueError(f"{name} must not be blank.")
    return clean_value


def _required_https_url(value: str, name: str) -> str:
    clean_value = _required_config_string(value, name).rstrip("/")
    parsed_url = urlsplit(clean_value)
    if parsed_url.scheme != "https":
        raise ValueError(f"{name} must be an https:// URL.")
    if parsed_url.hostname is None:
        raise ValueError(f"{name} must be a valid https URL with a host.")
    if parsed_url.username is not None or parsed_url.password is not None:
        raise ValueError(f"{name} must not include credentials.")
    if parsed_url.query or parsed_url.fragment:
        raise ValueError(f"{name} must not include a query string or fragment.")
    return clean_value
