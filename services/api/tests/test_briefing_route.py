"""GET /v1/doctor/sessions/{id}/briefing (M1-16): doctors only, stored or built on demand.

Needs Postgres (`make up`) for the stored and on-demand cases; skipped without it, like the other
DB tests. The FHIR document is not written here (no write-back yet: `skipped`); the document
itself is tested in services/integration.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, cast

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from careflow_api.briefing.service import BriefingService
from careflow_api.briefing.store import SqlBriefingStore
from careflow_api.security import ROLES, Role, create_access_token
from careflow_api.writeback_store import SqlWritebackStore
from careflow_contracts.testing import error_messages, openapi_validator
from careflow_intake.briefing.record import load_persona
from careflow_intake.state import IntakeState
from careflow_integration.fhir.client import FhirClient

ROUTE = "/v1/doctor/sessions/{}/briefing"


def bearer(role: Role, clinic_id: uuid.UUID) -> dict[str, str]:
    extra = {"session_id": "ses_test"} if role == "patient_session" else {"device_id": "dev_test"}
    token, _ = create_access_token(
        secret=os.environ["JWT_SECRET"],
        subject=f"sub-{role}",
        role=role,
        clinic_id=clinic_id,
        extra=extra,
    )
    return {"Authorization": f"Bearer {token}"}


class States:
    """The session's intake state, as M1-12's checkpointer will give it."""

    def __init__(self, *states: IntakeState) -> None:
        self.states = {state["session_id"]: state for state in states}
        self.loads = 0

    async def load(self, clinic_id: str, session_id: str) -> IntakeState | None:
        self.loads += 1
        return self.states.get(session_id)


def service(states: States | None = None) -> BriefingService:
    return BriefingService(
        store=SqlBriefingStore(),
        writebacks=SqlWritebackStore(),
        fhir=lambda: FhirClient("http://fhir.invalid/fhir", retry_delays=()),
        summarizer=None,
        states=states,
    )


def new_session(database: Engine, clinic_id: uuid.UUID) -> uuid.UUID:
    session_id = uuid.uuid4()
    with database.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.clinic_id', :c, true)"), {"c": str(clinic_id)}
        )
        connection.execute(
            text(
                "INSERT INTO patient_session (id, clinic_id, language, expires_at) "
                "VALUES (:id, :c, 'en', now() + interval '2 hours')"
            ),
            {"id": session_id, "c": clinic_id},
        )
    return session_id


def persona_state(persona: str, clinic_id: uuid.UUID, session_id: uuid.UUID) -> IntakeState:
    """A persona's intake state under the API's ids (the clinic's and the session's UUIDs)."""
    state = load_persona(persona)
    state["clinic_id"], state["session_id"] = str(clinic_id), str(session_id)
    decision = state.get("nurse_decision")
    assert decision is not None
    state["nurse_decision"] = decision.model_copy(update={"session_id": str(session_id)})
    return state


@pytest.fixture
def clinic(new_clinic: Any) -> uuid.UUID:
    return cast(uuid.UUID, new_clinic())


@pytest.mark.parametrize("role", [r for r in ROLES if r != "doctor"])
async def test_only_a_doctor_may_read_a_briefing(client: AsyncClient, role: str) -> None:
    response = await client.get(
        ROUTE.format(uuid.uuid4()), headers=bearer(cast(Role, role), uuid.uuid4())
    )
    assert response.status_code == 403, response.text
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_without_a_token_it_is_401(client: AsyncClient) -> None:
    assert (await client.get(ROUTE.format(uuid.uuid4()))).status_code == 401


async def test_the_doctor_gets_the_stored_briefing(
    app: FastAPI, client: AsyncClient, database: Engine, clinic: uuid.UUID
) -> None:
    session = new_session(database, clinic)
    app.state.briefings = service()
    built = await app.state.briefings.make(persona_state("chest-pain", clinic, session))
    response = await client.get(ROUTE.format(session), headers=bearer("doctor", clinic))
    assert response.status_code == 200, response.text
    body = response.json()
    assert error_messages(openapi_validator("Briefing"), body) == []
    assert body["session_id"] == str(session) == built.session_id
    assert body["summary_status"] == "disabled"
    assert [s["id"] for s in body["sections"]][0] == "alerts_triage"
    with database.begin() as connection:
        connection.execute(text("SELECT set_config('app.clinic_id', :c, true)"), {"c": str(clinic)})
        row = connection.execute(
            text(
                "SELECT summary_status, source_missing, document_status, document_error "
                "FROM briefings WHERE session_id = :s"
            ),
            {"s": session},
        ).one()
    assert tuple(row) == ("disabled", 0, "skipped", "the triage is not written back yet")


async def test_the_briefing_is_built_on_demand_and_then_stored(
    app: FastAPI, client: AsyncClient, database: Engine, clinic: uuid.UUID
) -> None:
    session = new_session(database, clinic)
    states = States(persona_state("sepsis", clinic, session))
    app.state.briefings = service(states)
    first = await client.get(ROUTE.format(session), headers=bearer("doctor", clinic))
    second = await client.get(ROUTE.format(session), headers=bearer("doctor", clinic))
    assert (first.status_code, second.status_code) == (200, 200), first.text
    assert first.json() == second.json()
    assert states.loads == 1
    assert error_messages(openapi_validator("Briefing"), first.json()) == []


async def test_another_clinics_session_is_not_found(
    app: FastAPI, client: AsyncClient, database: Engine, clinic: uuid.UUID, new_clinic: Any
) -> None:
    session = new_session(database, clinic)
    app.state.briefings = service(States(persona_state("routine", clinic, session)))
    await app.state.briefings.make(persona_state("routine", clinic, session))
    other = cast(uuid.UUID, new_clinic())
    response = await client.get(ROUTE.format(session), headers=bearer("doctor", other))
    assert response.status_code == 404, response.text


async def test_an_unconfirmed_or_unknown_session_is_not_found(
    app: FastAPI, client: AsyncClient, database: Engine, clinic: uuid.UUID
) -> None:
    session = new_session(database, clinic)
    state = persona_state("routine", clinic, session)
    state["nurse_decision"] = None
    app.state.briefings = service(States(state))
    waiting = await client.get(ROUTE.format(session), headers=bearer("doctor", clinic))
    assert waiting.status_code == 404
    assert waiting.json()["detail"] == "The nurse has not confirmed this session yet."
    unknown = await client.get(ROUTE.format(uuid.uuid4()), headers=bearer("doctor", clinic))
    not_a_uuid = await client.get(ROUTE.format("ses_chest_pain"), headers=bearer("doctor", clinic))
    assert (unknown.status_code, not_a_uuid.status_code) == (404, 404)
    assert unknown.json()["detail"] == "No briefing for this session."
