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
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from careflow_api.config import Settings, get_settings
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


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"}
    )


async def get_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("A valid bearer token is required.")
    try:
        claims = decode_token(
            credentials.credentials, secret=settings.jwt_secret.get_secret_value()
        )
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
        )
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise _unauthorized("Invalid or expired token.") from exc


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
    """One transaction per request, scoped by RLS to the caller's clinic."""
    async with clinic_session(principal.clinic_id) as session:
        yield session
