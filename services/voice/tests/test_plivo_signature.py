import base64
import hashlib
import hmac
from typing import Any

import pytest

from voice_agent.security.plivo_signature import (
    build_v3_signature,
    build_websocket_v3_signature,
    validate_v3_signature,
    validate_websocket_v3_signature,
)


def test_build_v3_signature_rejects_blank_required_inputs() -> None:
    valid_arguments: dict[str, Any] = {
        "method": "POST",
        "url": "https://voice.example.com/plivo/status",
        "nonce": "nonce-1",
        "auth_token": "plivo-auth-token",
    }

    for field_name in valid_arguments:
        arguments = dict(valid_arguments)
        arguments[field_name] = "   "

        with pytest.raises(ValueError, match=f"{field_name} is required"):
            build_v3_signature(**arguments)


def test_validate_v3_signature_rejects_blank_auth_token_without_empty_secret_match() -> None:
    signed_payload = "https://voice.example.com/plivo/status.nonce-1"
    signature = base64.b64encode(
        hmac.new(b"", signed_payload.encode("utf-8"), hashlib.sha256).digest()
    ).decode("ascii")

    assert not validate_v3_signature(
        method="GET",
        url="https://voice.example.com/plivo/status",
        nonce="nonce-1",
        auth_token="",
        signature_header=signature,
    )


def test_validate_v3_signature_rejects_blank_required_inputs() -> None:
    signature = build_v3_signature(
        method="GET",
        url="https://voice.example.com/plivo/status",
        nonce="nonce-1",
        auth_token="plivo-auth-token",
    )
    valid_arguments: dict[str, Any] = {
        "method": "GET",
        "url": "https://voice.example.com/plivo/status",
        "nonce": "nonce-1",
        "auth_token": "plivo-auth-token",
        "signature_header": signature,
    }

    for field_name in valid_arguments:
        arguments = dict(valid_arguments)
        arguments[field_name] = "   "

        assert not validate_v3_signature(**arguments)


def test_validate_v3_signature_rejects_non_ascii_signature_header() -> None:
    assert not validate_v3_signature(
        method="GET",
        url="https://voice.example.com/plivo/status",
        nonce="nonce-1",
        auth_token="plivo-auth-token",
        signature_header="é",
    )


def test_validate_v3_signature_accepts_plivo_sdk_post_payload_without_query() -> None:
    url = "https://voice.example.com/plivo/answer"
    nonce = "nonce-1"
    auth_token = "plivo-auth-token"
    params = {
        "CallUUID": "call-1",
        "From": "+918031339311",
        "To": "+917022989740",
    }
    signed_payload = f"{url}?CallUUIDcall-1From+918031339311To+917022989740.{nonce}"
    signature = base64.b64encode(
        hmac.new(
            auth_token.encode("utf-8"),
            signed_payload.encode("utf-8"),
            hashlib.sha256,
        ).digest()
    ).decode("ascii")

    assert validate_v3_signature(
        method="POST",
        url=url,
        nonce=nonce,
        auth_token=auth_token,
        signature_header=signature,
        params=params,
    )


def test_build_websocket_v3_signature_rejects_blank_required_inputs() -> None:
    valid_arguments = {
        "url": "wss://voice.example.com/plivo/media",
        "nonce": "nonce-1",
        "auth_token": "plivo-auth-token",
    }

    for field_name in valid_arguments:
        arguments = dict(valid_arguments)
        arguments[field_name] = "   "

        with pytest.raises(ValueError, match=f"{field_name} is required"):
            build_websocket_v3_signature(**arguments)


def test_validate_websocket_v3_signature_rejects_empty_secret_signature_match() -> None:
    signed_payload = "GETwss://voice.example.com/plivo/medianonce-1"
    signature = base64.b64encode(
        hmac.new(b"", signed_payload.encode("utf-8"), hashlib.sha256).digest()
    ).decode("ascii")

    assert not validate_websocket_v3_signature(
        url="wss://voice.example.com/plivo/media",
        nonce="nonce-1",
        auth_token="",
        signature_header=signature,
    )


def test_validate_websocket_v3_signature_rejects_blank_required_inputs() -> None:
    signature = build_websocket_v3_signature(
        url="wss://voice.example.com/plivo/media",
        nonce="nonce-1",
        auth_token="plivo-auth-token",
    )
    valid_arguments = {
        "url": "wss://voice.example.com/plivo/media",
        "nonce": "nonce-1",
        "auth_token": "plivo-auth-token",
        "signature_header": signature,
    }

    for field_name in valid_arguments:
        arguments = dict(valid_arguments)
        arguments[field_name] = "   "

        assert not validate_websocket_v3_signature(**arguments)


def test_validate_websocket_v3_signature_rejects_non_ascii_signature_header() -> None:
    assert not validate_websocket_v3_signature(
        url="wss://voice.example.com/plivo/media",
        nonce="nonce-1",
        auth_token="plivo-auth-token",
        signature_header="é",
    )
