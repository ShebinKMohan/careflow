"""Row-level security: clinic isolation, fail closed, and a guard for every future table."""

from __future__ import annotations

import os
import uuid
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends, FastAPI
from httpx import AsyncClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from careflow_api.deps import get_db
from careflow_api.models import PatientSession
from careflow_api.security import create_access_token

pytestmark = pytest.mark.usefixtures("clinics")


def app_engine() -> Engine:
    """careflow_app on careflow_test: the role the API uses (never an owner or superuser)."""
    return create_engine(
        os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://", 1)
    )


def count_sessions(clinic: uuid.UUID | None, only: uuid.UUID | None = None) -> int:
    engine = app_engine()
    try:
        with engine.begin() as connection:
            if clinic is not None:
                connection.execute(
                    text("SELECT set_config('app.clinic_id', :c, true)"), {"c": str(clinic)}
                )
            query = "SELECT count(*) FROM patient_session"
            params: dict[str, object] = {}
            if only is not None:
                query += " WHERE clinic_id = :only"
                params["only"] = only
            return int(connection.execute(text(query), params).scalar_one())
    finally:
        engine.dispose()


def test_clinic_a_sees_its_own_rows(clinics) -> None:  # noqa: ANN001
    assert count_sessions(clinics.a) == 1


def test_clinic_a_sees_no_clinic_b_rows(clinics) -> None:  # noqa: ANN001
    assert count_sessions(clinics.a, only=clinics.b) == 0


def test_without_app_clinic_id_nothing_is_visible() -> None:
    assert count_sessions(None) == 0


def test_inserting_a_clinic_b_row_from_clinic_a_fails(clinics) -> None:  # noqa: ANN001
    engine = app_engine()
    try:
        with pytest.raises(DBAPIError, match="row-level security"), engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.clinic_id', :c, true)"), {"c": str(clinics.a)}
            )
            connection.execute(
                text(
                    "INSERT INTO patient_session (clinic_id, language, expires_at) "
                    "VALUES (:c, 'en', now())"
                ),
                {"c": clinics.b},
            )
    finally:
        engine.dispose()


def test_the_app_role_is_not_privileged() -> None:
    engine = app_engine()
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            ).one()
    finally:
        engine.dispose()
    assert tuple(row) == (False, False)


def test_every_clinic_scoped_table_forces_rls(database: Engine) -> None:
    """Guard for later migrations: a table with clinic_id must force RLS and isolate by clinic."""
    with database.connect() as connection:
        tables = connection.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' AND EXISTS ("
                "  SELECT 1 FROM information_schema.columns col WHERE col.table_schema = 'public'"
                "  AND col.table_name = c.relname AND col.column_name = 'clinic_id')"
            )
        ).all()
        policies = {
            (row.tablename, row.policyname)
            for row in connection.execute(text("SELECT tablename, policyname FROM pg_policies"))
        }
    assert tables, "expected clinic-scoped tables"
    for name, enabled, forced in tables:
        assert enabled and forced, f"{name}: RLS must be enabled and forced"
        assert (name, "clinic_isolation") in policies, f"{name}: missing clinic_isolation policy"


async def test_a_clinic_a_token_through_get_db_sees_and_writes_only_clinic_a(
    app: FastAPI,
    client: AsyncClient,
    clinics,  # noqa: ANN001
) -> None:
    """The request path end to end: JWT -> get_principal -> get_db (app.clinic_id) -> RLS."""
    probe = APIRouter()

    @probe.get("/probe/patient-sessions")
    async def count(
        db: Annotated[AsyncSession, Depends(get_db)], clinic: uuid.UUID
    ) -> dict[str, int]:
        query = select(func.count()).select_from(PatientSession)
        return {"rows": int(await db.scalar(query.where(PatientSession.clinic_id == clinic)) or 0)}

    @probe.post("/probe/patient-sessions")
    async def add(
        db: Annotated[AsyncSession, Depends(get_db)], clinic: uuid.UUID
    ) -> dict[str, str]:
        try:
            async with db.begin_nested():
                await db.execute(
                    text(
                        "INSERT INTO patient_session (clinic_id, language, expires_at) "
                        "VALUES (:c, 'en', now())"
                    ),
                    {"c": clinic},
                )
        except DBAPIError as exc:
            return {"result": "rejected by RLS" if "row-level security" in str(exc) else "error"}
        return {"result": "inserted"}

    app.include_router(probe)
    token, _ = create_access_token(
        secret=os.environ["JWT_SECRET"], subject="sub-nurse", role="nurse", clinic_id=clinics.a
    )
    headers = {"Authorization": f"Bearer {token}"}

    async def rows(clinic: uuid.UUID) -> object:
        response = await client.get(
            "/probe/patient-sessions", params={"clinic": str(clinic)}, headers=headers
        )
        return response.json()

    assert await rows(clinics.a) == {"rows": 1}
    assert await rows(clinics.b) == {"rows": 0}
    inserted = await client.post(
        "/probe/patient-sessions", params={"clinic": str(clinics.b)}, headers=headers
    )
    assert inserted.json() == {"result": "rejected by RLS"}
    assert await rows(clinics.b) == {"rows": 0}
