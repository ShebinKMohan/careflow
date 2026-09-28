"""The kiosk channel's pieces: tokens (as the API issues them), replies, the turns client."""

from __future__ import annotations

import json
import secrets
import uuid
from typing import Any

import httpx
import pytest
import respx

from careflow_api import security
from careflow_contracts.api_models import TurnResponse
from voice_agent.kiosk.auth import (
    ALGORITHM,
    REQUIRED_CLAIMS,
    KioskAuthError,
    PatientSession,
    verify_patient_token,
)
from voice_agent.kiosk.loop import next_prompt
from voice_agent.kiosk.turns import TurnError, TurnsClient

SECRET = secrets.token_urlsafe(32)  # a fresh fake secret for each test run
API = "http://api.test"


def test_voice_deadline_covers_the_apis_extraction_and_question_budgets() -> None:
    from careflow_intake.wiring import IntakeConfig
    from voice_agent.config.settings import DEFAULT_KIOSK_API_TIMEOUT_SECONDS

    intake = IntakeConfig()
    # A voice request must survive the API's bounded extraction and fallback question. The
    # former ten-second client deadline abandoned accepted turns before the API could reply.
    assert DEFAULT_KIOSK_API_TIMEOUT_SECONDS > (
        intake.extraction_timeout_s + intake.phrasing_timeout_s
    )


def test_the_token_rules_are_the_apis() -> None:
    assert security.ALGORITHM == ALGORITHM
    assert list(security.REQUIRED_CLAIMS) == list(REQUIRED_CLAIMS)


def test_a_patient_token_issued_by_the_api_is_accepted_for_its_own_session_only() -> None:
    clinic = uuid.uuid4()
    token, _ = security.create_access_token(
        secret=SECRET,
        subject="ses_abc",
        role="patient_session",
        clinic_id=clinic,
        extra={"session_id": "ses_abc"},
    )
    session = verify_patient_token(token, session_id="ses_abc", secret=SECRET)
    assert session == PatientSession(session_id="ses_abc", clinic_id=str(clinic), token=token)
    assert token not in repr(session)
    with pytest.raises(KioskAuthError, match="another session"):
        verify_patient_token(token, session_id="ses_other", secret=SECRET)


def test_a_staff_token_is_refused() -> None:
    token, _ = security.create_access_token(
        secret=SECRET, subject="nurse_1", role="nurse", clinic_id=uuid.uuid4()
    )
    with pytest.raises(KioskAuthError, match="not a patient"):
        verify_patient_token(token, session_id="ses_abc", secret=SECRET)


QUESTION = {"question_id": "q_pain_score_1", "text": "How bad is it?", "key": "pain_score"}


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (
            {"next_question": {**QUESTION, "notice": "A nurse is coming."}},
            (
                "A nurse is coming. How bad is it?",
                {**QUESTION, "notice": "A nurse is coming."},
                False,
            ),
        ),
        (
            {"next_question": {**QUESTION, "notice": None}},
            ("How bad is it?", {**QUESTION, "notice": None}, False),
        ),
        (
            {"next_question": None, "done": True, "assistant_text": "Thank you."},
            ("Thank you.", None, True),
        ),
        ({"done": True, "assistant_text": "Thank you."}, ("Thank you.", None, True)),
        (
            {"assistant_text": "Where is the pain?"},
            (
                "Where is the pain?",
                {
                    "question_id": "turn_01",
                    "text": "Where is the pain?",
                    "key": None,
                    "notice": None,
                },
                False,
            ),
        ),
    ],
    ids=["notice", "question", "done", "older-api-done", "older-api-question"],
)
def test_next_prompt_follows_the_kiosk_fallbacks(
    reply: dict[str, Any], expected: tuple[str, dict[str, Any] | None, bool]
) -> None:
    body = {
        "turn_id": "turn_01",
        "intake_revision": 3,
        "assistant_text": "x",
        "done": False,
        **reply,
    }
    assert next_prompt(TurnResponse.model_validate(body)) == expected


async def test_the_turns_client_posts_the_contract_body() -> None:
    session = PatientSession(session_id="ses_abc", clinic_id="c", token="tok")
    with respx.mock(base_url=API) as api:
        route = api.post("/v1/sessions/ses_abc/turns").respond(
            200,
            json={"turn_id": "turn_01", "intake_revision": 2, "assistant_text": "ok", "done": True},
        )
        client = TurnsClient(base_url=API + "/", timeout_seconds=1.0)
        reply = await client.post(session, text="x" * 2500, language="en")
        await client.aclose()
    assert reply.done is True
    assert reply.intake_revision == 2
    body = json.loads(route.calls[0].request.content)
    assert body == {"input_mode": "voice", "text": "x" * 2000, "language": "en"}
    assert route.calls[0].request.headers["authorization"] == "Bearer tok"


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(422, json={"title": "bad"}), "turn_rejected"),
        (httpx.Response(500), "api_unavailable"),
        (httpx.Response(200, text="not json"), "bad_api_response"),
        (httpx.Response(200, json={"turn_id": "t"}), "bad_api_response"),
    ],
)
async def test_the_turns_client_maps_failures_to_codes(response: httpx.Response, code: str) -> None:
    session = PatientSession(session_id="ses_abc", clinic_id="c", token="tok")
    with respx.mock(base_url=API) as api:
        api.post("/v1/sessions/ses_abc/turns").mock(return_value=response)
        client = TurnsClient(base_url=API, timeout_seconds=1.0)
        with pytest.raises(TurnError) as failure:
            await client.post(session, text="hello", language="en")
        await client.aclose()
    assert failure.value.code == code
