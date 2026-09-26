"""SqlWritebackStore (M1-09) on the careflow_test database: status, resources and clinic isolation.

Needs Postgres (`make up`); skipped without it, like the other DB tests. The `app` fixture gives
each test a fresh engine on its own event loop.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import Engine

from careflow_api.writeback_store import SqlWritebackStore
from careflow_integration.fhir.writeback.ports import (
    WritebackOutcome,
    WritebackStatus,
    WrittenResource,
)

ENCOUNTER = WrittenResource("encounter", "Encounter", "101", "1", created=True)
TASK = WrittenResource("task-ecg_within_10_min", "Task", "113", "1", created=True)


@pytest.fixture(scope="module")
def own_clinics(database: Engine) -> tuple[uuid.UUID, uuid.UUID]:
    """Two clinics of this module's own: the shared clinics' row counts stay as test_rls expects."""
    ids = (uuid.uuid4(), uuid.uuid4())
    with database.begin() as connection:
        for clinic_id in ids:
            connection.execute(
                text("INSERT INTO clinic (id, name, timezone) VALUES (:id, :name, 'Asia/Dubai')"),
                {"id": clinic_id, "name": f"writeback-{clinic_id.hex[:8]}"},
            )
    return ids


def new_session(database: Engine, clinic_id: uuid.UUID) -> str:
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
    return str(session_id)


async def test_a_write_back_records_its_status_and_resources(
    app: FastAPI, database: Engine, own_clinics: tuple[uuid.UUID, uuid.UUID]
) -> None:
    clinic = str(own_clinics[0])
    session = new_session(database, own_clinics[0])
    store = SqlWritebackStore()
    assert await store.status(clinic, session) is None
    ok = WritebackOutcome(
        clinic, session, WritebackStatus.ok, 2, bundle_id="b-1", resources=(ENCOUNTER, TASK)
    )
    await store.save(ok)
    assert await store.status(clinic, session) is WritebackStatus.ok
    assert await store.resources(clinic, session) == [
        WrittenResource("encounter", "Encounter", "101", "1"),
        WrittenResource("task-ecg_within_10_min", "Task", "113", "1"),
    ]
    await store.update_resource(
        clinic, session, WrittenResource("task-ecg_within_10_min", "Task", "113", "2")
    )
    assert [r.version_id for r in await store.resources(clinic, session)] == ["1", "2"]
    # A later failed attempt replaces the status and keeps the resources already written.
    await store.save(
        WritebackOutcome(clinic, session, WritebackStatus.failed, 4, error="ConnectError")
    )
    assert await store.status(clinic, session) is WritebackStatus.failed
    assert len(await store.resources(clinic, session)) == 2
    with database.begin() as connection:
        connection.execute(text("SELECT set_config('app.clinic_id', :c, true)"), {"c": clinic})
        row = connection.execute(
            text(
                "SELECT status, attempts, last_error FROM fhir_writeback_status "
                "WHERE session_id = :s"
            ),
            {"s": session},
        ).one()
    assert tuple(row) == ("failed", 4, "ConnectError")


async def test_another_clinic_sees_none_of_it(
    app: FastAPI, database: Engine, own_clinics: tuple[uuid.UUID, uuid.UUID]
) -> None:
    mine, other = (str(c) for c in own_clinics)
    session = new_session(database, own_clinics[0])
    store = SqlWritebackStore()
    await store.save(WritebackOutcome(mine, session, WritebackStatus.ok, 1, resources=(ENCOUNTER,)))
    assert await store.resources(other, session) == []
    assert await store.status(other, session) is None
    assert await store.resources(mine, session) != []
