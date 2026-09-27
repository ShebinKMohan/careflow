"""Request dependencies reused by later routers: get_principal, require_roles, get_db.

The HTTPBearer pattern is ported from the owner's voice-dashboard control plane (`app/deps.py`);
`ROLE_RANK` is replaced by an explicit `require_roles(*roles)`. Tokens never travel in a URL.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from careflow_api.config import Settings, get_settings
from careflow_api.handover import handed_over
from careflow_api.rls import clinic_session
from careflow_api.security import ROLES, Role, decode_token

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    role: Role
    clinic_id: uuid.UUID
    token_id: str
    device_id: str | None = None
    session_id: str | None = None
    #: A patient token's channel (M2-06): "kiosk" (keypad, ID tap; also a token without the claim)
    #: or "phone" (the QR hand-over).
    channel: str | None = None


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"}
    )


async def get_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("A valid bearer token is required.")
    principal = _principal(credentials.credentials, settings)
    if principal.role == "patient_session" and principal.channel != "phone":
        # After the QR hand-over the kiosk's tokens for that session are revoked (M2-06).
        redis = getattr(request.app.state, "redis", None)
        if (
            redis is not None
            and principal.session_id
            and await handed_over(redis, principal.session_id)
        ):
            raise _unauthorized("This session continued on the patient's phone.")
    return principal


def _principal(token: str, settings: Settings) -> Principal:
    try:
        claims = decode_token(token, secret=settings.jwt_secret.get_secret_value())
        role = claims["role"]
        if role not in ROLES:
            raise ValueError("unknown role")
        return Principal(
            subject=str(claims["sub"]),
            role=role,
            clinic_id=uuid.UUID(str(claims["clinic_id"])),
            token_id=str(claims["jti"]),
            device_id=claims.get("device_id"),
            session_id=claims.get("session_id"),
            channel=claims.get("channel"),
        )
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise _unauthorized("Invalid or expired token.") from exc


async def require_phone_session(
    principal: Annotated[Principal, Depends(get_principal)],
) -> Principal:
    """Only a patient token handed over to the phone (M2-06): the phone-only features (M2-07,
    M2-09). A kiosk device or a kiosk-channel patient token gets 403."""
    if principal.role != "patient_session" or principal.channel != "phone":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This route is for the phone only.")
    return principal


def require_roles(*roles: Role) -> Callable[..., Awaitable[Principal]]:
    """Allow only the listed roles (403 otherwise; 401 without a valid token)."""
    allowed = frozenset(roles)

    async def dependency(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        if principal.role not in allowed:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail="This role may not call this route."
            )
        return principal

    return dependency


async def get_db(
    principal: Annotated[Principal, Depends(get_principal)],
) -> AsyncIterator[AsyncSession]:
    """One transaction per request, scoped by RLS to the caller's clinic. Inject it as `Db`."""
    async with clinic_session(principal.clinic_id) as session:
        yield session


# `scope="function"`: the transaction commits when the route returns, BEFORE the response is sent.
# FastAPI's default runs a yield dependency's exit after the response, so a client that acted on
# a 200 at once (the nurse's "Seen", then the worklist's reload) could read the state from before
# the commit (SHE-325).
Db = Annotated[AsyncSession, Depends(get_db, scope="function")]
