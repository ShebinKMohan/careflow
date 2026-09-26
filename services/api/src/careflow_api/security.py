"""Passwords (bcrypt) and access tokens (PyJWT, HS256).

Ported from the owner's voice-dashboard control plane (`app/security.py`). Tokens carry sub, role,
clinic_id, exp, iat and jti, plus device_id or session_id where relevant. Never logged.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Final, Literal

import bcrypt
import jwt

Role = Literal[
    "kiosk_device", "patient_session", "nurse", "doctor", "admin", "board", "integration"
]
ROLES: Final[tuple[Role, ...]] = (
    "kiosk_device", "patient_session", "nurse", "doctor", "admin", "board", "integration",
)  # fmt: skip
TOKEN_TTL: Final[dict[Role, dt.timedelta]] = {
    "nurse": dt.timedelta(hours=12),
    "doctor": dt.timedelta(hours=12),
    "admin": dt.timedelta(hours=12),
    "patient_session": dt.timedelta(hours=2),
    "kiosk_device": dt.timedelta(hours=24),
    "board": dt.timedelta(hours=24),
    "integration": dt.timedelta(hours=1),
}
ALGORITHM: Final = "HS256"
REQUIRED_CLAIMS: Final = ["sub", "role", "clinic_id", "exp", "iat", "jti"]


def hash_password(plaintext: str) -> str:
    return bcrypt.hashpw(plaintext.encode(), bcrypt.gensalt()).decode()


def verify_password(plaintext: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plaintext.encode(), hashed.encode())
    except ValueError:  # malformed hash, or a password over bcrypt's 72-byte limit
        return False


def create_access_token(
    *,
    secret: str,
    subject: str,
    role: Role,
    clinic_id: uuid.UUID | str,
    extra: dict[str, Any] | None = None,
    now: dt.datetime | None = None,
) -> tuple[str, int]:
    """Return the token and its lifetime in seconds."""
    issued = now or dt.datetime.now(tz=dt.UTC)
    ttl = TOKEN_TTL[role]
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "clinic_id": str(clinic_id),
        "iat": issued,
        "exp": issued + ttl,
        "jti": uuid.uuid4().hex,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, secret, algorithm=ALGORITHM), int(ttl.total_seconds())


def decode_token(token: str, *, secret: str) -> dict[str, Any]:
    """Verify the signature and expiry and require every claim; raises jwt.PyJWTError."""
    return jwt.decode(token, secret, algorithms=[ALGORITHM], options={"require": REQUIRED_CLAIMS})
