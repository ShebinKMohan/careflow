"""7 roles + anonymous x {me, nurse worklist, admin status} -> the expected 200/401/403 matrix."""

from __future__ import annotations

import os
import uuid
from typing import cast

import pytest
from httpx import AsyncClient

from careflow_api.security import ROLES, Role, create_access_token

ROUTES = ("/v1/auth/me", "/v1/nurse/worklist", "/v1/admin/status")
ALLOWED = {
    "/v1/auth/me": set(ROLES),
    "/v1/nurse/worklist": {"nurse", "admin"},
    "/v1/admin/status": {"admin"},
}
CASES = [(role, route) for role in (*ROLES, None) for route in ROUTES]


def expected(role: str | None, route: str) -> int:
    if role is None:
        return 401
    return 200 if role in ALLOWED[route] else 403


def bearer(role: Role) -> dict[str, str]:
    extra = {"session_id": "ses_test"} if role == "patient_session" else {"device_id": "dev_test"}
    token, _ = create_access_token(
        secret=os.environ["JWT_SECRET"],
        subject=f"sub-{role}",
        role=role,
        clinic_id=uuid.uuid4(),
        extra=extra,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize(
    ("role", "route"), CASES, ids=[f"{r or 'anonymous'}-{p}" for r, p in CASES]
)
async def test_role_matrix(client: AsyncClient, role: str | None, route: str) -> None:
    headers = bearer(cast(Role, role)) if role else {}
    response = await client.get(route, headers=headers)
    assert response.status_code == expected(role, route), response.text
    if response.status_code != 200:
        assert response.headers["content-type"].startswith("application/problem+json")


async def test_me_echoes_the_token(client: AsyncClient) -> None:
    response = await client.get("/v1/auth/me", headers=bearer("doctor"))
    assert response.json()["role"] == "doctor"
    assert response.json()["user_id"] == "sub-doctor"


async def test_a_token_in_the_query_string_is_ignored(client: AsyncClient) -> None:
    token = bearer("admin")["Authorization"].split(" ", 1)[1]
    response = await client.get(f"/v1/admin/status?access_token={token}")
    assert response.status_code == 401


async def test_garbage_token_is_401(client: AsyncClient) -> None:
    response = await client.get("/v1/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert response.status_code == 401
