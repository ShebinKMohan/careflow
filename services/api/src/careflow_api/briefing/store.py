"""`briefings` on Postgres (M1-16): one row per session, read and written under the clinic's
row-level security. A rebuilt briefing replaces the row; the FHIR document's outcome is kept."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from careflow_api.models import BriefingRow
from careflow_api.rls import clinic_session
from careflow_contracts.api_models import Briefing
from careflow_intake.briefing.builder import briefing_json
from careflow_integration.fhir.writeback.composition import DocumentOutcome


class SqlBriefingStore:
    async def get(self, clinic_id: uuid.UUID, session_id: uuid.UUID) -> dict[str, Any] | None:
        """The stored briefing JSON, or None."""
        query = select(BriefingRow.briefing).where(BriefingRow.session_id == session_id)
        async with clinic_session(clinic_id) as db:
            found = (await db.execute(query)).scalar_one_or_none()
        return None if found is None else dict(found)

    async def save(
        self,
        clinic_id: uuid.UUID,
        session_id: uuid.UUID,
        briefing: Briefing,
        *,
        source_missing: int,
    ) -> None:
        body = briefing_json(briefing)
        statement = insert(BriefingRow).values(
            clinic_id=clinic_id,
            session_id=session_id,
            briefing=body,
            summary_status=briefing.summary_status.value,
            source_missing=source_missing,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[BriefingRow.session_id],
            set_={
                "briefing": statement.excluded.briefing,
                "summary_status": statement.excluded.summary_status,
                "source_missing": statement.excluded.source_missing,
                "updated_at": func.now(),
            },
        )
        async with clinic_session(clinic_id) as db:
            await db.execute(statement)

    async def set_document(
        self, clinic_id: uuid.UUID, session_id: uuid.UUID, outcome: DocumentOutcome
    ) -> None:
        statement = (
            update(BriefingRow)
            .where(BriefingRow.session_id == session_id)
            .values(
                document_status=outcome.status.value,
                document_id=outcome.bundle_id,
                document_error=outcome.error,
                updated_at=func.now(),
            )
        )
        async with clinic_session(clinic_id) as db:
            await db.execute(statement)

    async def document(
        self, clinic_id: uuid.UUID, session_id: uuid.UUID
    ) -> tuple[str | None, str | None]:
        """The FHIR document's status and Bundle id (the admin page, M1-17)."""
        query = select(BriefingRow.document_status, BriefingRow.document_id).where(
            BriefingRow.session_id == session_id
        )
        async with clinic_session(clinic_id) as db:
            row = (await db.execute(query)).one_or_none()
        return (None, None) if row is None else (row[0], row[1])


__all__ = ["SqlBriefingStore"]
