"""`WritebackStore` on Postgres (M1-09): each session's FHIR write-back status and the resources
it wrote, read and written under the clinic's row-level security. `status()` serves the admin
page (M1-17). Session and clinic ids are the API's UUIDs."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.sql.dml import Insert

from careflow_api.models import FhirWriteback, FhirWritebackStatus
from careflow_api.rls import clinic_session
from careflow_integration.fhir.writeback.ports import (
    WritebackOutcome,
    WritebackStatus,
    WrittenResource,
)


def _upsert(clinic_id: uuid.UUID, session_id: uuid.UUID, resource: WrittenResource) -> Insert:
    statement = insert(FhirWriteback).values(
        clinic_id=clinic_id,
        session_id=session_id,
        local_id=resource.local_id,
        resource_type=resource.resource_type,
        resource_id=resource.resource_id,
        version_id=resource.version_id,
    )
    return statement.on_conflict_do_update(
        index_elements=[FhirWriteback.session_id, FhirWriteback.local_id],
        set_={
            "resource_type": statement.excluded.resource_type,
            "resource_id": statement.excluded.resource_id,
            "version_id": statement.excluded.version_id,
            "updated_at": func.now(),
        },
    )


class SqlWritebackStore:
    async def save(self, outcome: WritebackOutcome) -> None:
        clinic_id, session_id = uuid.UUID(outcome.clinic_id), uuid.UUID(outcome.session_id)
        status = insert(FhirWritebackStatus).values(
            clinic_id=clinic_id,
            session_id=session_id,
            status=outcome.status.value,
            attempts=outcome.attempts,
            bundle_id=outcome.bundle_id,
            last_error=outcome.error,
        )
        status = status.on_conflict_do_update(
            index_elements=[FhirWritebackStatus.session_id],
            set_={
                "status": status.excluded.status,
                "attempts": status.excluded.attempts,
                "bundle_id": status.excluded.bundle_id,
                "last_error": status.excluded.last_error,
                "updated_at": func.now(),
            },
        )
        async with clinic_session(clinic_id) as db:
            await db.execute(status)
            for resource in outcome.resources:
                await db.execute(_upsert(clinic_id, session_id, resource))

    async def resources(self, clinic_id: str, session_id: str) -> list[WrittenResource]:
        query = (
            select(FhirWriteback)
            .where(FhirWriteback.session_id == uuid.UUID(session_id))
            .order_by(FhirWriteback.local_id)
        )
        async with clinic_session(uuid.UUID(clinic_id)) as db:
            rows = (await db.execute(query)).scalars().all()
        return [
            WrittenResource(r.local_id, r.resource_type, r.resource_id, r.version_id) for r in rows
        ]

    async def update_resource(
        self, clinic_id: str, session_id: str, resource: WrittenResource
    ) -> None:
        clinic, session = uuid.UUID(clinic_id), uuid.UUID(session_id)
        async with clinic_session(clinic) as db:
            await db.execute(_upsert(clinic, session, resource))

    async def status(self, clinic_id: str, session_id: str) -> WritebackStatus | None:
        query = select(FhirWritebackStatus.status).where(
            FhirWritebackStatus.session_id == uuid.UUID(session_id)
        )
        async with clinic_session(uuid.UUID(clinic_id)) as db:
            found = (await db.execute(query)).scalar_one_or_none()
        return None if found is None else WritebackStatus(found)


__all__ = ["SqlWritebackStore"]
