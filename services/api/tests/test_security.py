"""Passwords and tokens (no database)."""

from __future__ import annotations

import datetime as dt

import jwt
import pytest

from careflow_api.security import (
    ROLES,
    TOKEN_TTL,
    Role,
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)

SECRET = "unit-test-secret-000000000000000000000"


def test_password_round_trip() -> None:
    hashed = hash_password("correct horse")
    assert hashed.startswith("$2b$")
    assert verify_password("correct horse", hashed)
    assert not verify_password("wrong", hashed)
    assert not verify_password("anything", "not-a-bcrypt-hash")


@pytest.mark.parametrize("role", ROLES)
def test_token_claims_and_lifetime(role: Role) -> None:
    now = dt.datetime(2026, 9, 26, 9, 0, tzinfo=dt.UTC)
    token, ttl = create_access_token(
        secret=SECRET,
        subject="s1",
        role=role,
        clinic_id="00000000-0000-0000-0000-00000000000a",
        now=now,
        extra={"device_id": "d1"},
    )
    claims = jwt.decode(token, SECRET, algorithms=["HS256"], options={"verify_exp": False})
    assert set(claims) >= {"sub", "role", "clinic_id", "exp", "iat", "jti", "device_id"}
    assert claims["exp"] - claims["iat"] == ttl == int(TOKEN_TTL[role].total_seconds())


def test_lifetimes_match_the_issue() -> None:
    hours = {role: TOKEN_TTL[role].total_seconds() / 3600 for role in ROLES}
    assert hours == {
        "kiosk_device": 24, "patient_session": 2, "nurse": 12, "doctor": 12, "admin": 12,
        "board": 24, "integration": 1,
    }  # fmt: skip


def test_expired_wrong_secret_and_missing_claims_are_rejected() -> None:
    old = dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=2)
    expired, _ = create_access_token(
        secret=SECRET, subject="s", role="nurse", clinic_id="c", now=old
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(expired, secret=SECRET)
    fresh, _ = create_access_token(secret=SECRET, subject="s", role="nurse", clinic_id="c")
    with pytest.raises(jwt.InvalidSignatureError):
        decode_token(fresh, secret="another-secret-0000000000000000000")
    bare = jwt.encode({"sub": "s", "exp": 9999999999}, SECRET, algorithm="HS256")
    with pytest.raises(jwt.MissingRequiredClaimError):
        decode_token(bare, secret=SECRET)
