"""The QR hand-over's security on the real stack (M2-06, SHE-300, ADR-037, ADR-048).

The real routes, Postgres, Valkey and the intake graph: the `flow` app from conftest on the
isolated test stack, with a test-only signing key. What a code, a claim and the tokens may do:

- a code is refused when expired, forged or for another audience (401), and when spent, when its
  session is closed, or when the session was handed over before (409), also after the 2-hour
  Redis mark is gone (the graph remembers);
- the phone that claimed may ask again with its claim key while the code is valid (its first
  answer was lost) and gets a working credential; anyone else gets 409;
- the phone's token works only on its own session (403 elsewhere); the kiosk's patient token of a
  handed-over session gets 401, and the kiosk can no longer issue a code (409);
- `require_phone_session` refuses a device, a kiosk-channel and a channel-less token (403);
- renewal gives the phone a new token; a kiosk token or a device gets 403, an expired token 401,
  an ended or 12-hour-old visit 409;
- the claim is rate-limited per address, and the kiosk's channel gets `session.handed_over`.

Synthetic personas only. The first answer (the complaint) is replayed from the flow cassette.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import secrets
import uuid
from collections.abc import Iterator
from typing import Any, cast

import jwt
import pytest
from fastapi import HTTPException
from redis import Redis
from sqlalchemy import text
from sqlalchemy.engine import Engine
from starlette.testclient import TestClient

from careflow_api import handover, ratelimit
from careflow_api.config import get_settings
from careflow_api.deps import Principal, require_phone_session
from careflow_api.events.frames import SESSION_HANDED_OVER
from careflow_api.security import Role, create_access_token, decode_token

COMPLAINT = "I've had chest pain since this morning"  # replayed from the flow cassette
CHEST_PAIN_DIGITS = "100001"
DEVICE_SECRET = "test-device-secret-000000"  # conftest.TEST_DEVICE_SECRET
JWT_SECRET = "test-only-jwt-secret-not-used-anywhere-else-0000"  # conftest.TEST_JWT_SECRET
HANDOVER_KEY = "test-only-handover-signing-key-000000000000000000"  # conftest.TEST_HANDOVER_KEY


@pytest.fixture(scope="module")
def clinics(clinics_factory: Any) -> Any:
    """This module's own clinics: its check-ins must not add sessions to the shared ones."""
    return clinics_factory()


@pytest.fixture(autouse=True)
def fresh_limits() -> Iterator[None]:
    """Each test its own rate-limit buckets: the module claims more than 10 codes a minute."""
    ratelimit.reset_memory()
    ratelimit.key_prefix = f"cf:handover:{uuid.uuid4().hex}"
    yield


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def kiosk_device(client: TestClient, clinics: Any, letter: str = "a") -> dict[str, str]:
    body = {
        "device_id": str(clinics.devices[(letter, "kiosk")]),
        "device_type": "kiosk",
        "device_secret": DEVICE_SECRET,
    }
    response = client.post("/v1/auth/device-token", json=body)
    assert response.status_code == 200, response.text
    return bearer(response.json()["access_token"])


def check_in(client: TestClient, clinics: Any, device: dict[str, str]) -> tuple[str, str]:
    """A keypad check-in of the chest-pain persona at clinic A's kiosk: (session, kiosk token)."""
    body = {
        "clinic_id": str(clinics.a),
        "kiosk_id": str(clinics.devices[("a", "kiosk")]),
        "digits": CHEST_PAIN_DIGITS,
        "language": "en",
    }
    response = client.post("/v1/checkin/keypad", json=body, headers=device)
    assert response.status_code == 200, response.text
    return str(response.json()["session_id"]), str(response.json()["session_token"])


def turn(client: TestClient, session_id: str, token: str, mode: str, answer: str) -> Any:
    body = {"input_mode": mode, "text": answer, "language": "en"}
    return client.post(f"/v1/sessions/{session_id}/turns", json=body, headers=bearer(token))


def issue_code(client: TestClient, session_id: str, headers: dict[str, str]) -> Any:
    return client.post(f"/v1/sessions/{session_id}/qr", headers=headers)


def code_for(client: TestClient, session_id: str, kiosk_token: str) -> str:
    response = issue_code(client, session_id, bearer(kiosk_token))
    assert response.status_code == 200, response.text
    return str(response.json()["qr_token"])


def claim(client: TestClient, code: str, key: str | None = None) -> Any:
    body: dict[str, str] = {"qr_token": code}
    if key is not None:
        body["claim_key"] = key
    return client.post("/v1/checkin/qr/claim", json=body)


def new_key() -> str:
    return secrets.token_urlsafe(32)  # 43 characters, as the phone makes it


def session_update(database: Engine, clinic_id: uuid.UUID, session_id: str, **values: Any) -> None:
    assignments = ", ".join(f"{name} = :{name}" for name in values)
    with database.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.clinic_id', :c, true)"), {"c": str(clinic_id)}
        )
        connection.execute(
            text(f"UPDATE patient_session SET {assignments} WHERE id = :id"),  # noqa: S608
            {"id": session_id, **values},
        )


@pytest.fixture
def started(flow: TestClient, clinics: Any) -> tuple[str, str, dict[str, str]]:
    """A checked-in session that answered its first question on the kiosk."""
    device = kiosk_device(flow, clinics)
    session_id, kiosk_token = check_in(flow, clinics, device)
    assert turn(flow, session_id, kiosk_token, "text", COMPLAINT).status_code == 200
    return session_id, kiosk_token, device


# Codes


def test_an_expired_forged_or_foreign_code_is_refused(
    flow: TestClient, clinics: Any, started: tuple[str, str, dict[str, str]]
) -> None:
    session_id, _, _ = started
    long_ago = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=131)  # 120 s + 10 s of leeway
    expired, _ = handover.issue(
        HANDOVER_KEY, session_id=session_id, clinic_id=str(clinics.a), now=long_ago
    )
    assert claim(flow, expired, new_key()).status_code == 401
    forged, _ = handover.issue(
        "another-key-" + "0" * 40,
        session_id=session_id,
        clinic_id=str(clinics.a),
        now=dt.datetime.now(dt.UTC),
    )
    assert claim(flow, forged, new_key()).status_code == 401
    now = int(dt.datetime.now(dt.UTC).timestamp())
    foreign = jwt.encode(
        {
            "sid": session_id,
            "clinic_id": str(clinics.a),
            "jti": uuid.uuid4().hex,
            "aud": "careflow-staff",
            "iat": now,
            "exp": now + 120,
        },
        HANDOVER_KEY,
        algorithm="HS256",
    )
    assert claim(flow, foreign, new_key()).status_code == 401


def test_a_spent_code_is_refused_to_anyone_but_the_phone_that_claimed_it(
    flow: TestClient, started: tuple[str, str, dict[str, str]]
) -> None:
    session_id, kiosk_token, _ = started
    code, key = code_for(flow, session_id, kiosk_token), new_key()
    first = claim(flow, code, key)
    assert first.status_code == 200, first.text
    assert claim(flow, code).status_code == 409  # a replay without the key
    assert claim(flow, code, new_key()).status_code == 409  # another phone

    # The first answer was lost: the same phone asks again and gets a working credential.
    again = claim(flow, code, key)
    assert again.status_code == 200, again.text
    assert again.json()["session_id"] == session_id
    assert again.json()["session_token"] != first.json()["session_token"]
    claims = decode_token(again.json()["session_token"], secret=JWT_SECRET)
    assert (claims["session_id"], claims["channel"]) == (session_id, "phone")
    status = flow.get(
        f"/v1/sessions/{session_id}/status", headers=bearer(again.json()["session_token"])
    )
    assert status.status_code == 200, status.text
    assert status.json()["current_question"] == again.json()["first_question"]


def test_a_code_claimed_without_a_key_cannot_be_claimed_again(
    flow: TestClient, started: tuple[str, str, dict[str, str]]
) -> None:
    session_id, kiosk_token, _ = started
    code = code_for(flow, session_id, kiosk_token)
    assert claim(flow, code).status_code == 200
    assert claim(flow, code).status_code == 409
    assert claim(flow, code, new_key()).status_code == 409


def test_a_claim_after_the_session_closed_is_refused(
    flow: TestClient, clinics: Any, database: Engine, started: tuple[str, str, dict[str, str]]
) -> None:
    session_id, kiosk_token, device = started
    code = code_for(flow, session_id, kiosk_token)
    session_update(database, clinics.a, session_id, status="closed")
    assert claim(flow, code, new_key()).status_code == 409
    assert issue_code(flow, session_id, device).status_code == 409


def test_a_handed_over_session_refuses_any_other_code_even_after_its_redis_mark_is_gone(
    flow: TestClient, clinics: Any, started: tuple[str, str, dict[str, str]]
) -> None:
    session_id, kiosk_token, device = started
    first, second = (code_for(flow, session_id, kiosk_token) for _ in range(2))
    assert claim(flow, first, new_key()).status_code == 200
    assert claim(flow, second, new_key()).status_code == 409
    assert issue_code(flow, session_id, device).status_code == 409

    redis = Redis.from_url(os.environ["REDIS_URL"])
    try:
        redis.delete(f"handover:session:{session_id}")  # as if its 2 hours had passed
    finally:
        redis.close()
    third_code, _ = handover.issue(
        HANDOVER_KEY, session_id=session_id, clinic_id=str(clinics.a), now=dt.datetime.now(dt.UTC)
    )
    assert claim(flow, second, new_key()).status_code == 409  # still spent
    assert claim(flow, third_code, new_key()).status_code == 409  # the graph: on the phone
    assert issue_code(flow, session_id, device).status_code == 409


# Tokens after the hand-over


def test_the_kiosk_token_stops_and_the_phone_token_stays_in_its_session(
    flow: TestClient, clinics: Any, started: tuple[str, str, dict[str, str]]
) -> None:
    session_id, kiosk_token, device = started
    other_id, _ = check_in(flow, clinics, device)
    phone = claim(flow, code_for(flow, session_id, kiosk_token), new_key()).json()
    phone_token = phone["session_token"]

    # The kiosk's patient token is revoked everywhere; the phone's works.
    assert turn(flow, session_id, kiosk_token, "tap", "No").status_code == 401
    kiosk_status = flow.get(f"/v1/sessions/{session_id}/status", headers=bearer(kiosk_token))
    assert kiosk_status.status_code == 401
    assert issue_code(flow, session_id, bearer(kiosk_token)).status_code == 401
    assert flow.get(f"/v1/sessions/{session_id}/status", headers=bearer(phone_token)).is_success

    # The phone token for this session is refused on another one.
    other = bearer(phone_token)
    assert turn(flow, other_id, phone_token, "tap", "No").status_code == 403
    assert flow.get(f"/v1/sessions/{other_id}/status", headers=other).status_code == 403
    assert issue_code(flow, other_id, other).status_code == 403
    assert flow.post(f"/v1/sessions/{other_id}/renew", headers=other).status_code == 403
    # And a phone never issues a code, not even for its own session.
    assert issue_code(flow, session_id, other).status_code == 403


def test_the_phone_continues_the_same_intake_after_the_claim(
    flow: TestClient, started: tuple[str, str, dict[str, str]]
) -> None:
    session_id, kiosk_token, _ = started
    before = flow.get(f"/v1/sessions/{session_id}/status", headers=bearer(kiosk_token)).json()
    phone = claim(flow, code_for(flow, session_id, kiosk_token), new_key()).json()
    assert phone["first_question"] == before["current_question"]  # where the kiosk stopped
    answer = turn(flow, session_id, phone["session_token"], "tap", "Yes")
    assert answer.status_code == 200, answer.text


def test_the_kiosk_channel_hears_the_hand_over(
    flow: TestClient, started: tuple[str, str, dict[str, str]]
) -> None:
    session_id, kiosk_token, _ = started
    assert claim(flow, code_for(flow, session_id, kiosk_token), new_key()).status_code == 200
    redis = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    try:
        entries = cast(
            "list[tuple[str, dict[str, str]]]", redis.xrange(get_settings().event_stream)
        )
    finally:
        redis.close()
    frames = [
        json.loads(fields["payload"])
        for _, fields in entries
        if fields.get("type") == SESSION_HANDED_OVER and fields.get("session_id") == session_id
    ]
    assert [(frame["type"], frame["data"]) for frame in frames] == [
        (SESSION_HANDED_OVER, {"session_id": session_id})
    ]


# require_phone_session


@pytest.mark.parametrize(
    ("role", "channel"),
    [("kiosk_device", None), ("patient_session", "kiosk"), ("patient_session", None)],
)
async def test_require_phone_session_refuses_anything_but_a_phone_token(
    role: Role, channel: str | None
) -> None:
    principal = Principal(
        subject="x",
        role=role,
        clinic_id=uuid.uuid4(),
        token_id="t",
        session_id=str(uuid.uuid4()),
        channel=channel,
    )
    with pytest.raises(HTTPException) as refused:
        await require_phone_session(principal)
    assert refused.value.status_code == 403


async def test_require_phone_session_admits_a_phone_token() -> None:
    principal = Principal(
        subject="x",
        role="patient_session",
        clinic_id=uuid.uuid4(),
        token_id="t",
        session_id=str(uuid.uuid4()),
        channel="phone",
    )
    assert await require_phone_session(principal) is principal


# Renewal


def test_the_phone_renews_its_token_while_the_visit_is_open(
    flow: TestClient, started: tuple[str, str, dict[str, str]]
) -> None:
    session_id, kiosk_token, device = started
    assert flow.post(f"/v1/sessions/{session_id}/renew", headers=device).status_code == 403
    before = flow.post(f"/v1/sessions/{session_id}/renew", headers=bearer(kiosk_token))
    assert before.status_code == 403  # a kiosk-channel token never renews
    phone_token = claim(flow, code_for(flow, session_id, kiosk_token), new_key()).json()[
        "session_token"
    ]
    renewed = flow.post(f"/v1/sessions/{session_id}/renew", headers=bearer(phone_token))
    assert renewed.status_code == 200, renewed.text
    token = renewed.json()["session_token"]
    claims = decode_token(token, secret=JWT_SECRET)
    assert (claims["role"], claims["session_id"], claims["channel"]) == (
        "patient_session",
        session_id,
        "phone",
    )
    expires_at = dt.datetime.fromisoformat(renewed.json()["expires_at"])
    assert abs((expires_at - dt.datetime.now(dt.UTC)) - dt.timedelta(minutes=30)) < dt.timedelta(
        seconds=30
    )
    assert int(expires_at.timestamp()) == claims["exp"]
    assert flow.get(f"/v1/sessions/{session_id}/status", headers=bearer(token)).is_success
    # The revoked kiosk token cannot come back through renewal either.
    after = flow.post(f"/v1/sessions/{session_id}/renew", headers=bearer(kiosk_token))
    assert after.status_code == 401


def test_an_expired_phone_token_cannot_renew(
    flow: TestClient, clinics: Any, started: tuple[str, str, dict[str, str]]
) -> None:
    session_id, kiosk_token, _ = started
    assert claim(flow, code_for(flow, session_id, kiosk_token), new_key()).status_code == 200
    expired, _ = create_access_token(
        secret=JWT_SECRET,
        subject=f"session:{session_id}",
        role="patient_session",
        clinic_id=clinics.a,
        extra={"session_id": session_id, "scope": f"session:{session_id}", "channel": "phone"},
        now=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=31),
        ttl=dt.timedelta(minutes=30),
    )
    assert flow.post(f"/v1/sessions/{session_id}/renew", headers=bearer(expired)).status_code == 401


def test_an_ended_or_twelve_hour_old_visit_cannot_renew(
    flow: TestClient, clinics: Any, database: Engine, started: tuple[str, str, dict[str, str]]
) -> None:
    session_id, kiosk_token, _ = started
    phone = bearer(
        claim(flow, code_for(flow, session_id, kiosk_token), new_key()).json()["session_token"]
    )
    long_ago = dt.datetime.now(dt.UTC) - dt.timedelta(hours=12, minutes=1)
    session_update(database, clinics.a, session_id, started_at=long_ago)
    assert flow.post(f"/v1/sessions/{session_id}/renew", headers=phone).status_code == 409
    session_update(database, clinics.a, session_id, started_at=dt.datetime.now(dt.UTC))
    assert flow.post(f"/v1/sessions/{session_id}/renew", headers=phone).status_code == 200
    for ended in ("closed", "no_show"):
        session_update(database, clinics.a, session_id, status=ended)
        assert flow.post(f"/v1/sessions/{session_id}/renew", headers=phone).status_code == 409


# Rate limit


def test_claims_are_rate_limited_per_address(flow: TestClient) -> None:
    statuses = [claim(flow, "not-a-real-code-" + str(n)).status_code for n in range(11)]
    assert statuses[:10] == [401] * 10
    assert statuses[10] == 429
