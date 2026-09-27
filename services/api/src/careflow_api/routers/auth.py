"""Auth: staff login, device tokens and `me` (/v1/auth/*).

Before a token exists no clinic is known, so the two lookups use narrow SELECT-only RLS
exceptions: the transaction sets `app.auth_email` (or `app.auth_device_id`) to the exact value
being checked, which exposes that one row and nothing else (ADR-014).
"""

from __future__ import annotations

import secrets
import uuid
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from careflow_api.config import Settings, get_settings
from careflow_api.deps import Principal, get_principal
from careflow_api.models import Device, StaffUser
from careflow_api.ratelimit import device_token_rate_limit, login_rate_limit
from careflow_api.rls import SET_AUTH_DEVICE, SET_AUTH_EMAIL, clinic_session
from careflow_api.security import Role, create_access_token, hash_password, verify_password
from careflow_contracts.api_models import DeviceTokenRequest, LoginRequest, Me, TokenResponse

router = APIRouter(prefix="/v1/auth", tags=["auth"])

DEVICE_ROLES: dict[str, Role] = {
    "kiosk": "kiosk_device",
    "board": "board",
    "integration": "integration",
}
_BAD_LOGIN = "Unknown user or wrong password."
_BAD_DEVICE = "Unknown device or wrong secret."


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """A real bcrypt hash of a random value: unknown users cost as much time as wrong passwords."""
    return hash_password(secrets.token_urlsafe(24))


def _token(settings: Settings, **kwargs: object) -> TokenResponse:
    token, ttl = create_access_token(secret=settings.jwt_secret.get_secret_value(), **kwargs)  # type: ignore[arg-type]
    return TokenResponse.model_validate(
        {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": ttl,
            "role": kwargs["role"],
            "clinic_id": str(kwargs["clinic_id"]),
        }
    )


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(login_rate_limit)])
async def login(
    body: LoginRequest, settings: Annotated[Settings, Depends(get_settings)]
) -> TokenResponse:
    email = body.username.strip().lower()
    async with clinic_session(None) as session:
        await session.execute(SET_AUTH_EMAIL, {"email": email})
        user = (
            await session.execute(select(StaffUser).where(StaffUser.email == email))
        ).scalar_one_or_none()
    if user is None or not user.is_active:
        verify_password(body.password, _dummy_hash())
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=_BAD_LOGIN)
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=_BAD_LOGIN)
    return _token(settings, subject=str(user.id), role=user.role, clinic_id=user.clinic_id)


@router.post(
    "/device-token", response_model=TokenResponse, dependencies=[Depends(device_token_rate_limit)]
)
async def device_token(
    body: DeviceTokenRequest, settings: Annotated[Settings, Depends(get_settings)]
) -> TokenResponse:
    try:
        device_id = uuid.UUID(body.device_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=_BAD_DEVICE) from exc
    async with clinic_session(None) as session:
        await session.execute(SET_AUTH_DEVICE, {"device_id": str(device_id)})
        device = (
            await session.execute(select(Device).where(Device.id == device_id))
        ).scalar_one_or_none()
    if (
        device is None
        or not device.is_active
        or device.kind != body.device_type.value
        or not verify_password(body.device_secret, device.secret_hash)
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=_BAD_DEVICE)
    return _token(
        settings,
        subject=str(device.id),
        role=DEVICE_ROLES[device.kind],
        clinic_id=device.clinic_id,
        extra={"device_id": str(device.id)},
    )


@router.get("/me", response_model=Me, response_model_exclude_none=True)
async def me(principal: Annotated[Principal, Depends(get_principal)]) -> Me:
    return Me.model_validate(
        {
            "user_id": principal.subject,
            "role": principal.role,
            "clinic_id": str(principal.clinic_id),
        }
    )
