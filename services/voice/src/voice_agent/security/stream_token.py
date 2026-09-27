from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

TOKEN_VERSION = "v1"


@dataclass(frozen=True, slots=True)
class StreamTokenClaims:
    call_id: str
    expires_at: int
    nonce: str = ""


def build_stream_token(
    *,
    signing_secret: str,
    ttl_seconds: int,
    call_id: str,
    cfg: str = "",
    now: int | None = None,
    nonce: str | None = None,
) -> str:
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive.")
    token_signing_secret = _require_token_field("signing_secret", signing_secret)
    token_call_id = _require_token_field("call_id", call_id)
    token_nonce = _require_token_field("nonce", nonce or secrets.token_urlsafe(18))
    if "." in token_call_id:
        raise ValueError("call_id must not contain '.'.")
    if "." in token_nonce:
        raise ValueError("nonce must not contain '.'.")
    issued_at = int(time.time()) if now is None else now
    expires_at = issued_at + ttl_seconds
    token_body = _token_body(
        expires_at=expires_at,
        call_id=token_call_id,
        nonce=token_nonce,
    )
    signature = _signature(token_signing_secret, _signing_input(token_body, cfg))
    return f"{token_body}.{signature}"


def validate_stream_token(
    *,
    token: str,
    signing_secret: str,
    call_id: str,
    cfg: str = "",
    now: int | None = None,
) -> bool:
    expected_call_id = _normalise_token_field(call_id)
    if expected_call_id is None:
        return False
    claims = verify_stream_token(
        token=token, signing_secret=signing_secret, cfg=cfg, now=now
    )
    return claims is not None and claims.call_id == expected_call_id


def verify_stream_token(
    *,
    token: str,
    signing_secret: str,
    cfg: str = "",
    now: int | None = None,
) -> StreamTokenClaims | None:
    clean_signing_secret = _normalise_token_field(signing_secret)
    if clean_signing_secret is None:
        return None
    parts = token.split(".")
    if len(parts) != 5:
        return None
    version, expires_at_text, token_call_id, nonce, supplied_signature = parts
    clean_call_id = _normalise_token_field(token_call_id)
    clean_nonce = _normalise_token_field(nonce)
    if (
        version != TOKEN_VERSION
        or clean_call_id is None
        or clean_nonce is None
        or not supplied_signature
    ):
        return None
    try:
        expires_at = int(expires_at_text)
    except ValueError:
        return None

    current_time = int(time.time()) if now is None else now
    if expires_at < current_time:
        return None

    token_body = _token_body(
        expires_at=expires_at,
        call_id=clean_call_id,
        nonce=clean_nonce,
    )
    expected_signature = _signature(clean_signing_secret, _signing_input(token_body, cfg))
    if not _signature_matches(supplied_signature, expected_signature):
        return None
    return StreamTokenClaims(
        call_id=clean_call_id, expires_at=expires_at, nonce=clean_nonce
    )


def _token_body(*, expires_at: int, call_id: str, nonce: str) -> str:
    return f"{TOKEN_VERSION}.{expires_at}.{call_id}.{nonce}"


def _signing_input(token_body: str, cfg: str) -> str:
    """Bind the per-call ``cfg`` id into the signed material as associated data, so a
    tampered/swapped cfg invalidates the signature. An empty cfg reproduces the
    pre-cfg signature, keeping older unsigned-cfg flows backward compatible."""
    clean_cfg = cfg.strip()
    return token_body if not clean_cfg else f"{token_body}.{clean_cfg}"


def _signature(signing_secret: str, signed_payload: str) -> str:
    digest = hmac.new(
        signing_secret.encode("utf-8"),
        signed_payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _signature_matches(supplied_signature: str, expected_signature: str) -> bool:
    try:
        supplied_signature.encode("ascii")
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(supplied_signature, expected_signature)


def _require_token_field(name: str, value: str) -> str:
    clean_value = _normalise_token_field(value)
    if clean_value is None:
        raise ValueError(f"{name} is required.")
    return clean_value


def _normalise_token_field(value: str) -> str | None:
    clean_value = value.strip()
    return clean_value if clean_value else None
