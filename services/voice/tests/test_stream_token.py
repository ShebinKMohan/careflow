import base64
import hashlib
import hmac

import pytest

from voice_agent.security.stream_token import (
    build_stream_token,
    validate_stream_token,
    verify_stream_token,
)


def test_stream_token_validates_before_expiry() -> None:
    token = build_stream_token(
        signing_secret="stream-secret",
        ttl_seconds=300,
        now=1_000,
        nonce="nonce-1",
        call_id="call-1",
    )

    assert validate_stream_token(
        token=token,
        signing_secret="stream-secret",
        now=1_299,
        call_id="call-1",
    )


def test_stream_token_rejects_expired_token() -> None:
    token = build_stream_token(
        signing_secret="stream-secret",
        ttl_seconds=300,
        now=1_000,
        nonce="nonce-1",
        call_id="call-1",
    )

    assert not validate_stream_token(
        token=token,
        signing_secret="stream-secret",
        now=1_301,
        call_id="call-1",
    )


def test_stream_token_rejects_tampered_signature() -> None:
    token = build_stream_token(
        signing_secret="stream-secret",
        ttl_seconds=300,
        now=1_000,
        nonce="nonce-1",
        call_id="call-1",
    )
    tampered = token.rsplit(".", maxsplit=1)[0] + ".invalid"

    assert not validate_stream_token(
        token=tampered,
        signing_secret="stream-secret",
        now=1_100,
        call_id="call-1",
    )


def test_stream_token_rejects_non_ascii_signature() -> None:
    token = "v1.1300.call-1.nonce-1.é"

    assert (
        verify_stream_token(
            token=token,
            signing_secret="stream-secret",
            now=1_100,
        )
        is None
    )
    assert not validate_stream_token(
        token=token,
        signing_secret="stream-secret",
        now=1_100,
        call_id="call-1",
    )


def test_stream_token_rejects_mismatched_call_id() -> None:
    token = build_stream_token(
        signing_secret="stream-secret",
        ttl_seconds=300,
        now=1_000,
        nonce="nonce-1",
        call_id="call-1",
    )

    assert not validate_stream_token(
        token=token,
        signing_secret="stream-secret",
        now=1_100,
        call_id="call-2",
    )


def test_stream_token_verification_returns_bound_call_claims() -> None:
    token = build_stream_token(
        signing_secret="stream-secret",
        ttl_seconds=300,
        now=1_000,
        nonce="nonce-1",
        call_id="call-1",
    )

    claims = verify_stream_token(token=token, signing_secret="stream-secret", now=1_100)

    assert claims is not None
    assert claims.call_id == "call-1"
    assert claims.expires_at == 1_300


def test_stream_token_builder_rejects_blank_call_id() -> None:
    with pytest.raises(ValueError, match="call_id is required"):
        build_stream_token(
            signing_secret="stream-secret",
            ttl_seconds=300,
            now=1_000,
            nonce="nonce-1",
            call_id="   ",
        )


def test_stream_token_builder_rejects_blank_signing_secret() -> None:
    with pytest.raises(ValueError, match="signing_secret is required"):
        build_stream_token(
            signing_secret="   ",
            ttl_seconds=300,
            now=1_000,
            nonce="nonce-1",
            call_id="call-1",
        )


def test_stream_token_verification_rejects_blank_signing_secret() -> None:
    signed_payload = "v1.1300.call-1.nonce-1"
    signature = (
        base64.urlsafe_b64encode(
            hmac.new(b"", signed_payload.encode("utf-8"), hashlib.sha256).digest()
        )
        .rstrip(b"=")
        .decode("ascii")
    )
    token = f"{signed_payload}.{signature}"

    assert (
        verify_stream_token(
            token=token,
            signing_secret="",
            now=1_100,
        )
        is None
    )
    assert not validate_stream_token(
        token=token,
        signing_secret="",
        now=1_100,
        call_id="call-1",
    )


def test_stream_token_builder_trims_call_id_before_signing() -> None:
    token = build_stream_token(
        signing_secret="stream-secret",
        ttl_seconds=300,
        now=1_000,
        nonce="nonce-1",
        call_id=" call-1 ",
    )

    assert validate_stream_token(
        token=token,
        signing_secret="stream-secret",
        now=1_100,
        call_id="call-1",
    )


def test_stream_token_rejects_delimiter_in_call_id_or_nonce() -> None:
    with pytest.raises(ValueError, match="call_id must not contain"):
        build_stream_token(
            signing_secret="stream-secret",
            ttl_seconds=300,
            now=1_000,
            nonce="nonce-1",
            call_id="call.1",
        )
    with pytest.raises(ValueError, match="nonce must not contain"):
        build_stream_token(
            signing_secret="stream-secret",
            ttl_seconds=300,
            now=1_000,
            nonce="nonce.1",
            call_id="call-1",
        )


def test_stream_token_binds_cfg_into_signature() -> None:
    token = build_stream_token(
        signing_secret="stream-secret",
        ttl_seconds=300,
        now=1_000,
        nonce="nonce-1",
        call_id="call-1",
        cfg="cfg-abc",
    )

    # The exact cfg the token was minted with verifies.
    claims = verify_stream_token(
        token=token, signing_secret="stream-secret", cfg="cfg-abc", now=1_100
    )
    assert claims is not None and claims.call_id == "call-1"
    # A swapped cfg invalidates the signature (cannot point the call at another config).
    assert (
        verify_stream_token(
            token=token, signing_secret="stream-secret", cfg="cfg-other", now=1_100
        )
        is None
    )
    # Dropping the cfg entirely is also a mismatch.
    assert (
        verify_stream_token(token=token, signing_secret="stream-secret", now=1_100)
        is None
    )


def test_stream_token_without_cfg_is_backward_compatible() -> None:
    # A token minted with no cfg verifies with the default (empty) cfg...
    token = build_stream_token(
        signing_secret="stream-secret",
        ttl_seconds=300,
        now=1_000,
        nonce="nonce-1",
        call_id="call-1",
    )
    assert verify_stream_token(token=token, signing_secret="stream-secret", now=1_100)
    # ...but an attacker cannot graft a cfg onto an unsigned-cfg token.
    assert (
        verify_stream_token(
            token=token, signing_secret="stream-secret", cfg="cfg-x", now=1_100
        )
        is None
    )


def test_validate_stream_token_rejects_blank_expected_call_id() -> None:
    token = build_stream_token(
        signing_secret="stream-secret",
        ttl_seconds=300,
        now=1_000,
        nonce="nonce-1",
        call_id="call-1",
    )

    assert not validate_stream_token(
        token=token,
        signing_secret="stream-secret",
        now=1_100,
        call_id="   ",
    )
