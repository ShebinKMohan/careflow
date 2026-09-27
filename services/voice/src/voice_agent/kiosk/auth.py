"""The start message's patient session token, checked the way the API checks it (M0-07)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import jwt

ALGORITHM: Final = "HS256"
REQUIRED_CLAIMS: Final = ("sub", "role", "clinic_id", "exp", "iat", "jti")
PATIENT_ROLE: Final = "patient_session"


class KioskAuthError(Exception):
    """The token is missing, invalid, expired, not a patient token, or for another session."""


@dataclass(frozen=True, slots=True)
class PatientSession:
    session_id: str
    clinic_id: str
    token: str = ""  # sent on to the API with each turn; never logged

    def __repr__(self) -> str:
        return f"PatientSession(session_id={self.session_id!r}, clinic_id={self.clinic_id!r})"


def verify_patient_token(token: str, *, session_id: str, secret: str) -> PatientSession:
    try:
        claims: dict[str, Any] = jwt.decode(
            token, secret, algorithms=[ALGORITHM], options={"require": list(REQUIRED_CLAIMS)}
        )
    except jwt.PyJWTError as exc:
        raise KioskAuthError("invalid or expired token") from exc
    if claims.get("role") != PATIENT_ROLE:
        raise KioskAuthError("not a patient session token")
    if claims.get("session_id") != session_id:
        raise KioskAuthError("the token belongs to another session")
    return PatientSession(session_id=session_id, clinic_id=str(claims["clinic_id"]), token=token)
