"""Login, device tokens, rate limiting and the auth lookups under RLS."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.usefixtures("clinics")
PASSWORD = "Test-Password-123"
DEVICE_SECRET = "test-device-secret-000000"


async def test_staff_login_issues_a_token_for_the_right_clinic_and_role(
    client: AsyncClient, clinics
) -> None:  # noqa: ANN001
    response = await client.post(
        "/v1/auth/login", json={"username": clinics.staff[("b", "doctor")], "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["role"], body["clinic_id"], body["token_type"]) == (
        "doctor",
        str(clinics.b),
        "Bearer",
    )
    assert body["expires_in"] == 12 * 3600
    me = await client.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.json()["clinic_id"] == str(clinics.b)


async def test_wrong_password_and_unknown_user_are_the_same_401(
    client: AsyncClient, clinics
) -> None:  # noqa: ANN001
    wrong = await client.post(
        "/v1/auth/login", json={"username": clinics.staff[("a", "nurse")], "password": "nope"}
    )
    unknown = await client.post(
        "/v1/auth/login", json={"username": "nobody@test.example", "password": "nope"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]


async def test_six_bad_logins_in_a_minute_return_429(client: AsyncClient, clinics) -> None:  # noqa: ANN001
    codes = []
    for _ in range(6):
        response = await client.post(
            "/v1/auth/login", json={"username": clinics.staff[("a", "admin")], "password": "bad"}
        )
        codes.append(response.status_code)
    assert codes == [401, 401, 401, 401, 401, 429]


@pytest.mark.parametrize(
    ("kind", "role", "hours"),
    [("kiosk", "kiosk_device", 24), ("board", "board", 24), ("integration", "integration", 1)],
)
async def test_device_tokens(
    client: AsyncClient, clinics, kind: str, role: str, hours: int
) -> None:  # noqa: ANN001
    device_id = str(clinics.devices[("a", kind)])
    response = await client.post(
        "/v1/auth/device-token",
        json={"device_id": device_id, "device_type": kind, "device_secret": DEVICE_SECRET},
    )
    assert response.status_code == 200, response.text
    assert (response.json()["role"], response.json()["expires_in"]) == (role, hours * 3600)


async def test_device_token_rejects_a_wrong_secret_or_kind(client: AsyncClient, clinics) -> None:  # noqa: ANN001
    device_id = str(clinics.devices[("a", "kiosk")])
    wrong_secret = await client.post(
        "/v1/auth/device-token",
        json={"device_id": device_id, "device_type": "kiosk", "device_secret": "x" * 20},
    )
    wrong_kind = await client.post(
        "/v1/auth/device-token",
        json={"device_id": device_id, "device_type": "board", "device_secret": DEVICE_SECRET},
    )
    assert wrong_secret.status_code == wrong_kind.status_code == 401


async def test_invalid_body_is_a_422_problem(client: AsyncClient) -> None:
    response = await client.post("/v1/auth/login", json={"username": "x"})
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["errors"]
