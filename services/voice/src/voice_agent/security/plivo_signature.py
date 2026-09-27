from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Mapping
from urllib.parse import parse_qs, urlsplit, urlunsplit

SignatureParamValue = str | list[str]


def build_v3_signature(
    *,
    method: str,
    url: str,
    nonce: str,
    auth_token: str,
    params: Mapping[str, str] | None = None,
) -> str:
    clean_method = _require_field("method", method)
    clean_url = _require_field("url", url)
    clean_nonce = _require_field("nonce", nonce)
    clean_auth_token = _require_field("auth_token", auth_token)
    signed_payload = _build_signed_payload(
        method=clean_method,
        url=clean_url,
        nonce=clean_nonce,
        params=params or {},
    )
    digest = hmac.new(
        clean_auth_token.encode("utf-8"),
        signed_payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def validate_v3_signature(
    *,
    method: str,
    url: str,
    nonce: str | None,
    auth_token: str,
    signature_header: str | None,
    main_account_signature_header: str | None = None,
    params: Mapping[str, str] | None = None,
) -> bool:
    clean_method = _normalise_field(method)
    clean_url = _normalise_field(url)
    clean_nonce = _normalise_field(nonce)
    clean_auth_token = _normalise_field(auth_token)
    if clean_method is None or clean_url is None or clean_nonce is None or clean_auth_token is None:
        return False
    expected = build_v3_signature(
        method=clean_method,
        url=clean_url,
        nonce=clean_nonce,
        auth_token=clean_auth_token,
        params=params,
    )
    signatures = _split_signature_headers(signature_header, main_account_signature_header)
    return any(_signature_matches(expected, candidate) for candidate in signatures)


def build_websocket_v3_signature(
    *,
    url: str,
    nonce: str,
    auth_token: str,
) -> str:
    clean_url = _require_field("url", url)
    clean_nonce = _require_field("nonce", nonce)
    clean_auth_token = _require_field("auth_token", auth_token)
    digest = hmac.new(
        clean_auth_token.encode("utf-8"),
        f"GET{clean_url}{clean_nonce}".encode(),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def validate_websocket_v3_signature(
    *,
    url: str,
    nonce: str | None,
    auth_token: str,
    signature_header: str | None,
    main_account_signature_header: str | None = None,
) -> bool:
    clean_url = _normalise_field(url)
    clean_nonce = _normalise_field(nonce)
    clean_auth_token = _normalise_field(auth_token)
    if clean_url is None or clean_nonce is None or clean_auth_token is None:
        return False
    expected = build_websocket_v3_signature(
        url=clean_url,
        nonce=clean_nonce,
        auth_token=clean_auth_token,
    )
    signatures = _split_signature_headers(signature_header, main_account_signature_header)
    return any(_signature_matches(expected, candidate) for candidate in signatures)


def _build_signed_payload(
    *,
    method: str,
    url: str,
    nonce: str,
    params: Mapping[str, str],
) -> str:
    clean_method = method.upper()
    base_url = _url_without_query(url)
    query_params = _query_params(url)
    if clean_method == "GET":
        merged_params = _merge_params(query_params, params)
        sorted_query_params = _sorted_query_params(merged_params)
        if sorted_query_params:
            return f"{base_url}?{sorted_query_params}.{nonce}"
        return f"{base_url}.{nonce}"

    if clean_method == "POST":
        sorted_query_params = _sorted_query_params(query_params)
        prefix = f"{base_url}?{sorted_query_params}" if sorted_query_params else base_url
        if params:
            separator = "." if sorted_query_params else "?"
            return f"{prefix}{separator}{_sorted_post_params(params)}.{nonce}"
        return f"{prefix}.{nonce}"

    return f"{base_url}.{nonce}"


def _url_without_query(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _query_params(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query, keep_blank_values=True)


def _merge_params(
    query_params: Mapping[str, list[str]],
    params: Mapping[str, str],
) -> dict[str, SignatureParamValue]:
    merged: dict[str, SignatureParamValue] = dict(query_params)
    merged.update(params)
    return merged


def _sorted_query_params(params: Mapping[str, SignatureParamValue]) -> str:
    parts: list[str] = []
    for key in sorted(params):
        value = params[key]
        if isinstance(value, list):
            parts.extend(f"{key}={item}" for item in sorted(value))
        else:
            parts.append(f"{key}={value}")
    return "&".join(parts)


def _sorted_post_params(params: Mapping[str, str]) -> str:
    return "".join(f"{key}{params[key]}" for key in sorted(params))


def _split_signature_headers(*headers: str | None) -> list[str]:
    signatures: list[str] = []
    for header in headers:
        if not header:
            continue
        signatures.extend(candidate.strip() for candidate in header.split(",") if candidate.strip())
    return signatures


def _signature_matches(expected: str, candidate: str) -> bool:
    try:
        candidate.encode("ascii")
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(expected, candidate)


def _require_field(name: str, value: str) -> str:
    clean_value = _normalise_field(value)
    if clean_value is None:
        raise ValueError(f"{name} is required.")
    return clean_value


def _normalise_field(value: str | None) -> str | None:
    if value is None:
        return None
    clean_value = value.strip()
    return clean_value if clean_value else None
