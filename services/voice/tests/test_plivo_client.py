import base64
import json
from typing import Any

import httpx
import pytest

from voice_agent.app import _build_outbound_call_client
from voice_agent.config.settings import Settings
from voice_agent.telephony.plivo_client import PlivoCallClientError, PlivoOutboundCallClient


def test_outbound_call_client_builder_uses_configured_plivo_api_limits() -> None:
    client = _build_outbound_call_client(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="memory",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            plivo_auth_id="plivo-auth-id",
            plivo_auth_token="plivo-auth-token",
            plivo_api_timeout_seconds=4.5,
            plivo_api_max_response_bytes=8192,
        )
    )

    assert isinstance(client, PlivoOutboundCallClient)
    assert client._timeout_seconds == 4.5
    assert client._max_response_bytes == 8192


def test_plivo_outbound_call_client_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        PlivoOutboundCallClient(
            auth_id="plivo-auth-id",
            auth_token="plivo-auth-token",
            timeout_seconds=0,
        )


def test_plivo_outbound_call_client_rejects_non_positive_max_response_bytes() -> None:
    with pytest.raises(ValueError, match="max_response_bytes must be positive"):
        PlivoOutboundCallClient(
            auth_id="plivo-auth-id",
            auth_token="plivo-auth-token",
            max_response_bytes=0,
        )


@pytest.mark.parametrize("field_name", ["auth_id", "auth_token", "api_base_url"])
def test_plivo_outbound_call_client_rejects_blank_constructor_values(field_name: str) -> None:
    arguments: dict[str, Any] = {
        "auth_id": "plivo-auth-id",
        "auth_token": "plivo-auth-token",
        "api_base_url": "https://api.plivo.com",
    }
    arguments[field_name] = "   "

    with pytest.raises(ValueError, match=f"{field_name} must not be blank"):
        PlivoOutboundCallClient(**arguments)


def test_plivo_outbound_call_client_rejects_non_https_api_base_url() -> None:
    with pytest.raises(ValueError, match="api_base_url must be an https:// URL"):
        PlivoOutboundCallClient(
            auth_id="plivo-auth-id",
            auth_token="plivo-auth-token",
            api_base_url="http://api.plivo.com",
        )


@pytest.mark.parametrize(
    ("api_base_url", "match"),
    [
        ("https://", "api_base_url must be a valid https URL with a host"),
        ("https:///v1", "api_base_url must be a valid https URL with a host"),
        ("https://user:pass@api.plivo.com", "api_base_url must not include credentials"),
        (
            "https://api.plivo.com?region=us",
            "api_base_url must not include a query string or fragment",
        ),
        (
            "https://api.plivo.com#v1",
            "api_base_url must not include a query string or fragment",
        ),
    ],
)
def test_plivo_outbound_call_client_rejects_malformed_api_base_url(
    api_base_url: str,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        PlivoOutboundCallClient(
            auth_id="plivo-auth-id",
            auth_token="plivo-auth-token",
            api_base_url=api_base_url,
        )


@pytest.mark.anyio
async def test_plivo_outbound_call_client_trims_constructor_values() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            202,
            json={
                "api_id": "api-1",
                "message": "call fired",
                "request_uuid": "request-1",
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plivo = PlivoOutboundCallClient(
        auth_id=" plivo-auth-id ",
        auth_token=" plivo-auth-token ",
        api_base_url=" https://api.plivo.com/ ",
        client=http_client,
    )

    await plivo.create_call(
        from_number="+14155550100",
        to_number="+919876543210",
        answer_url="https://voice.example.com/plivo/answer",
        answer_method="POST",
        hangup_url="https://voice.example.com/plivo/status",
        hangup_method="POST",
        hangup_on_ring_seconds=45,
        time_limit_seconds=900,
    )

    request = requests[0]
    assert str(request.url) == "https://api.plivo.com/v1/Account/plivo-auth-id/Call/"
    assert request.headers["authorization"] == (
        "Basic " + base64.b64encode(b"plivo-auth-id:plivo-auth-token").decode("ascii")
    )
    await http_client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "field_name",
    ["from_number", "to_number", "answer_url", "answer_method", "hangup_url", "hangup_method"],
)
async def test_plivo_outbound_call_client_rejects_blank_create_call_arguments(
    field_name: str,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            202,
            json={
                "api_id": "api-1",
                "message": "call fired",
                "request_uuid": "request-1",
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plivo = PlivoOutboundCallClient(
        auth_id="plivo-auth-id",
        auth_token="plivo-auth-token",
        client=http_client,
    )
    call_arguments: dict[str, Any] = {
        "from_number": "+14155550100",
        "to_number": "+919876543210",
        "answer_url": "https://voice.example.com/plivo/answer",
        "answer_method": "POST",
        "hangup_url": "https://voice.example.com/plivo/status",
        "hangup_method": "POST",
        "hangup_on_ring_seconds": 45,
        "time_limit_seconds": 900,
    }
    call_arguments[field_name] = "   "

    with pytest.raises(PlivoCallClientError, match=field_name):
        await plivo.create_call(**call_arguments)

    assert requests == []
    await http_client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("field_name", ["answer_method", "hangup_method"])
async def test_plivo_outbound_call_client_rejects_invalid_create_call_methods(
    field_name: str,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            202,
            json={
                "api_id": "api-1",
                "message": "call fired",
                "request_uuid": "request-1",
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plivo = PlivoOutboundCallClient(
        auth_id="plivo-auth-id",
        auth_token="plivo-auth-token",
        client=http_client,
    )
    call_arguments: dict[str, Any] = {
        "from_number": "+14155550100",
        "to_number": "+919876543210",
        "answer_url": "https://voice.example.com/plivo/answer",
        "answer_method": "POST",
        "hangup_url": "https://voice.example.com/plivo/status",
        "hangup_method": "POST",
        "hangup_on_ring_seconds": 45,
        "time_limit_seconds": 900,
    }
    call_arguments[field_name] = "PATCH"

    with pytest.raises(PlivoCallClientError, match=f"{field_name} must be GET or POST"):
        await plivo.create_call(**call_arguments)

    assert requests == []
    await http_client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("field_name", ["from_number", "to_number"])
async def test_plivo_outbound_call_client_rejects_invalid_create_call_phone_numbers(
    field_name: str,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            202,
            json={
                "api_id": "api-1",
                "message": "call fired",
                "request_uuid": "request-1",
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plivo = PlivoOutboundCallClient(
        auth_id="plivo-auth-id",
        auth_token="plivo-auth-token",
        client=http_client,
    )
    call_arguments: dict[str, Any] = {
        "from_number": "+14155550100",
        "to_number": "+919876543210",
        "answer_url": "https://voice.example.com/plivo/answer",
        "answer_method": "POST",
        "hangup_url": "https://voice.example.com/plivo/status",
        "hangup_method": "POST",
        "hangup_on_ring_seconds": 45,
        "time_limit_seconds": 900,
    }
    call_arguments[field_name] = "14155550100"

    with pytest.raises(PlivoCallClientError, match=f"{field_name} must be an E.164"):
        await plivo.create_call(**call_arguments)

    assert requests == []
    await http_client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("field_name", ["answer_url", "hangup_url"])
async def test_plivo_outbound_call_client_rejects_non_https_create_call_callback_urls(
    field_name: str,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            202,
            json={
                "api_id": "api-1",
                "message": "call fired",
                "request_uuid": "request-1",
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plivo = PlivoOutboundCallClient(
        auth_id="plivo-auth-id",
        auth_token="plivo-auth-token",
        client=http_client,
    )
    call_arguments: dict[str, Any] = {
        "from_number": "+14155550100",
        "to_number": "+919876543210",
        "answer_url": "https://voice.example.com/plivo/answer",
        "answer_method": "POST",
        "hangup_url": "https://voice.example.com/plivo/status",
        "hangup_method": "POST",
        "hangup_on_ring_seconds": 45,
        "time_limit_seconds": 900,
    }
    call_arguments[field_name] = f"http://voice.example.com/{field_name}"

    with pytest.raises(PlivoCallClientError, match=f"{field_name} must be an https:// URL"):
        await plivo.create_call(**call_arguments)

    assert requests == []
    await http_client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("field_name", ["answer_url", "hangup_url"])
@pytest.mark.parametrize(
    ("callback_url", "match"),
    [
        ("https://", "must be a valid https URL with a host"),
        ("https:///callback", "must be a valid https URL with a host"),
        ("https://user:pass@voice.example.com/callback", "must not include credentials"),
        ("https://voice.example.com/callback#fragment", "must not include a fragment"),
    ],
)
async def test_plivo_outbound_call_client_rejects_malformed_create_call_callback_urls(
    field_name: str,
    callback_url: str,
    match: str,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            202,
            json={
                "api_id": "api-1",
                "message": "call fired",
                "request_uuid": "request-1",
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plivo = PlivoOutboundCallClient(
        auth_id="plivo-auth-id",
        auth_token="plivo-auth-token",
        client=http_client,
    )
    call_arguments: dict[str, Any] = {
        "from_number": "+14155550100",
        "to_number": "+919876543210",
        "answer_url": "https://voice.example.com/plivo/answer",
        "answer_method": "POST",
        "hangup_url": "https://voice.example.com/plivo/status",
        "hangup_method": "POST",
        "hangup_on_ring_seconds": 45,
        "time_limit_seconds": 900,
    }
    call_arguments[field_name] = callback_url

    with pytest.raises(PlivoCallClientError, match=f"{field_name} {match}"):
        await plivo.create_call(**call_arguments)

    assert requests == []
    await http_client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("field_name", ["hangup_on_ring_seconds", "time_limit_seconds"])
async def test_plivo_outbound_call_client_rejects_non_positive_create_call_timing(
    field_name: str,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            202,
            json={
                "api_id": "api-1",
                "message": "call fired",
                "request_uuid": "request-1",
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plivo = PlivoOutboundCallClient(
        auth_id="plivo-auth-id",
        auth_token="plivo-auth-token",
        client=http_client,
    )
    call_arguments: dict[str, Any] = {
        "from_number": "+14155550100",
        "to_number": "+919876543210",
        "answer_url": "https://voice.example.com/plivo/answer",
        "answer_method": "POST",
        "hangup_url": "https://voice.example.com/plivo/status",
        "hangup_method": "POST",
        "hangup_on_ring_seconds": 45,
        "time_limit_seconds": 900,
    }
    call_arguments[field_name] = 0

    with pytest.raises(PlivoCallClientError, match=f"{field_name} must be positive"):
        await plivo.create_call(**call_arguments)

    assert requests == []
    await http_client.aclose()


@pytest.mark.anyio
async def test_plivo_outbound_call_client_posts_expected_create_call_request() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            202,
            json={
                "api_id": "api-1",
                "message": "call fired",
                "request_uuid": "request-1",
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plivo = PlivoOutboundCallClient(
        auth_id="plivo-auth-id",
        auth_token="plivo-auth-token",
        client=http_client,
    )

    result = await plivo.create_call(
        from_number="+14155550100",
        to_number="+919876543210",
        answer_url="https://voice.example.com/plivo/answer",
        answer_method="POST",
        hangup_url="https://voice.example.com/plivo/status",
        hangup_method="POST",
        hangup_on_ring_seconds=45,
        time_limit_seconds=900,
    )

    assert result.api_id == "api-1"
    assert result.message == "call fired"
    assert result.request_uuid == "request-1"
    request = requests[0]
    assert str(request.url) == "https://api.plivo.com/v1/Account/plivo-auth-id/Call/"
    assert request.headers["authorization"] == (
        "Basic " + base64.b64encode(b"plivo-auth-id:plivo-auth-token").decode("ascii")
    )
    assert json.loads(request.content) == {
        "from": "+14155550100",
        "to": "+919876543210",
        "answer_url": "https://voice.example.com/plivo/answer",
        "answer_method": "POST",
        "hangup_url": "https://voice.example.com/plivo/status",
        "hangup_method": "POST",
        "hangup_on_ring": 45,
        "time_limit": 900,
    }
    await http_client.aclose()


@pytest.mark.anyio
async def test_plivo_outbound_call_client_deletes_call_for_hangup_or_cancel() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plivo = PlivoOutboundCallClient(
        auth_id="plivo-auth-id",
        auth_token="plivo-auth-token",
        client=http_client,
    )

    await plivo.hangup_call("call-1")

    request = requests[0]
    assert request.method == "DELETE"
    assert str(request.url) == "https://api.plivo.com/v1/Account/plivo-auth-id/Call/call-1/"
    assert request.headers["authorization"] == (
        "Basic " + base64.b64encode(b"plivo-auth-id:plivo-auth-token").decode("ascii")
    )
    await http_client.aclose()


@pytest.mark.anyio
async def test_plivo_outbound_call_client_rejects_oversized_hangup_content_length() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            500,
            content=b"error",
            headers={"content-length": "100"},
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plivo = PlivoOutboundCallClient(
        auth_id="plivo-auth-id",
        auth_token="plivo-auth-token",
        client=http_client,
        max_response_bytes=16,
    )

    with pytest.raises(PlivoCallClientError, match="hangup response exceeds configured size"):
        await plivo.hangup_call("call-1")

    await http_client.aclose()


@pytest.mark.anyio
async def test_plivo_outbound_call_client_stops_reading_oversized_hangup_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(500, content=b"x" * 128)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plivo = PlivoOutboundCallClient(
        auth_id="plivo-auth-id",
        auth_token="plivo-auth-token",
        client=http_client,
        max_response_bytes=64,
    )

    with pytest.raises(PlivoCallClientError, match="hangup response exceeds configured size"):
        await plivo.hangup_call("call-1")

    await http_client.aclose()


@pytest.mark.anyio
async def test_plivo_outbound_call_client_rejects_malformed_create_call_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(202, content=b"not-json")

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plivo = PlivoOutboundCallClient(
        auth_id="plivo-auth-id",
        auth_token="plivo-auth-token",
        client=http_client,
    )

    with pytest.raises(PlivoCallClientError):
        await plivo.create_call(
            from_number="+14155550100",
            to_number="+919876543210",
            answer_url="https://voice.example.com/plivo/answer",
            answer_method="POST",
            hangup_url="https://voice.example.com/plivo/status",
            hangup_method="POST",
            hangup_on_ring_seconds=45,
            time_limit_seconds=900,
        )

    await http_client.aclose()


@pytest.mark.anyio
async def test_plivo_outbound_call_client_rejects_oversized_create_call_content_length() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            202,
            content=b"{}",
            headers={"content-length": "100"},
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plivo = PlivoOutboundCallClient(
        auth_id="plivo-auth-id",
        auth_token="plivo-auth-token",
        client=http_client,
        max_response_bytes=16,
    )

    with pytest.raises(PlivoCallClientError, match="response exceeds configured size limit"):
        await plivo.create_call(
            from_number="+14155550100",
            to_number="+919876543210",
            answer_url="https://voice.example.com/plivo/answer",
            answer_method="POST",
            hangup_url="https://voice.example.com/plivo/status",
            hangup_method="POST",
            hangup_on_ring_seconds=45,
            time_limit_seconds=900,
        )

    await http_client.aclose()


@pytest.mark.anyio
async def test_plivo_outbound_call_client_stops_reading_oversized_create_call_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            202,
            json={
                "api_id": "api-1",
                "message": "x" * 128,
                "request_uuid": "request-1",
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plivo = PlivoOutboundCallClient(
        auth_id="plivo-auth-id",
        auth_token="plivo-auth-token",
        client=http_client,
        max_response_bytes=64,
    )

    with pytest.raises(PlivoCallClientError, match="response exceeds configured size limit"):
        await plivo.create_call(
            from_number="+14155550100",
            to_number="+919876543210",
            answer_url="https://voice.example.com/plivo/answer",
            answer_method="POST",
            hangup_url="https://voice.example.com/plivo/status",
            hangup_method="POST",
            hangup_on_ring_seconds=45,
            time_limit_seconds=900,
        )

    await http_client.aclose()


@pytest.mark.anyio
async def test_plivo_outbound_call_client_preserves_empty_http_error_responses() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(429, content=b"", headers={"content-length": "0"})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plivo = PlivoOutboundCallClient(
        auth_id="plivo-auth-id",
        auth_token="plivo-auth-token",
        client=http_client,
        max_response_bytes=64,
    )

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await plivo.create_call(
            from_number="+14155550100",
            to_number="+919876543210",
            answer_url="https://voice.example.com/plivo/answer",
            answer_method="POST",
            hangup_url="https://voice.example.com/plivo/status",
            hangup_method="POST",
            hangup_on_ring_seconds=45,
            time_limit_seconds=900,
        )

    assert exc_info.value.response.status_code == 429
    assert exc_info.value.response.text == ""
    await http_client.aclose()


@pytest.mark.anyio
async def test_plivo_outbound_call_client_rejects_blank_create_call_response_fields() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            202,
            json={
                "api_id": "api-1",
                "message": "call fired",
                "request_uuid": "   ",
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plivo = PlivoOutboundCallClient(
        auth_id="plivo-auth-id",
        auth_token="plivo-auth-token",
        client=http_client,
    )

    with pytest.raises(PlivoCallClientError, match="request_uuid"):
        await plivo.create_call(
            from_number="+14155550100",
            to_number="+919876543210",
            answer_url="https://voice.example.com/plivo/answer",
            answer_method="POST",
            hangup_url="https://voice.example.com/plivo/status",
            hangup_method="POST",
            hangup_on_ring_seconds=45,
            time_limit_seconds=900,
        )

    await http_client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("request_uuid", ["request/1", "x" * 129])
async def test_plivo_outbound_call_client_rejects_unsafe_create_call_response_request_uuid(
    request_uuid: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            202,
            json={
                "api_id": "api-1",
                "message": "call fired",
                "request_uuid": request_uuid,
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plivo = PlivoOutboundCallClient(
        auth_id="plivo-auth-id",
        auth_token="plivo-auth-token",
        client=http_client,
    )

    with pytest.raises(PlivoCallClientError, match="request_uuid must be 1-128"):
        await plivo.create_call(
            from_number="+14155550100",
            to_number="+919876543210",
            answer_url="https://voice.example.com/plivo/answer",
            answer_method="POST",
            hangup_url="https://voice.example.com/plivo/status",
            hangup_method="POST",
            hangup_on_ring_seconds=45,
            time_limit_seconds=900,
        )

    await http_client.aclose()


@pytest.mark.anyio
async def test_plivo_outbound_call_client_trims_create_call_response_fields() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            202,
            json={
                "api_id": " api-1 ",
                "message": " call fired ",
                "request_uuid": " request-1 ",
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plivo = PlivoOutboundCallClient(
        auth_id="plivo-auth-id",
        auth_token="plivo-auth-token",
        client=http_client,
    )

    result = await plivo.create_call(
        from_number="+14155550100",
        to_number="+919876543210",
        answer_url="https://voice.example.com/plivo/answer",
        answer_method="POST",
        hangup_url="https://voice.example.com/plivo/status",
        hangup_method="POST",
        hangup_on_ring_seconds=45,
        time_limit_seconds=900,
    )

    assert result.api_id == "api-1"
    assert result.message == "call fired"
    assert result.request_uuid == "request-1"
    await http_client.aclose()


@pytest.mark.anyio
async def test_plivo_outbound_call_client_rejects_blank_hangup_identifier() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plivo = PlivoOutboundCallClient(
        auth_id="plivo-auth-id",
        auth_token="plivo-auth-token",
        client=http_client,
    )

    with pytest.raises(PlivoCallClientError, match="identifier is required"):
        await plivo.hangup_call("   ")

    assert requests == []
    await http_client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "identifier",
    [
        "call/1",
        "call 1",
        "call\n1",
        "a" * 129,
    ],
)
async def test_plivo_outbound_call_client_rejects_unsafe_hangup_identifier(
    identifier: str,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plivo = PlivoOutboundCallClient(
        auth_id="plivo-auth-id",
        auth_token="plivo-auth-token",
        client=http_client,
    )

    with pytest.raises(PlivoCallClientError, match="identifier must be 1-128"):
        await plivo.hangup_call(identifier)

    assert requests == []
    await http_client.aclose()
