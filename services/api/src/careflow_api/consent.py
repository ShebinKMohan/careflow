"""Consent records (M2-05): what a patient agreed to at the kiosk.

Every POST appends a row (`consent_records`, clinic-scoped by RLS); the latest one wins. Other
modules call `get_consent` before anything uses the camera or the microphone: the voice-turn route
here, and the voice service through `GET /v1/sessions/{id}/consent`. A session without a record
has consented to nothing. Recording emits the internal `consent.recorded` event for the audit log
(M5-01).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Final

from sqlalchemy import select

from careflow_api.events.outbox import OutboxPublisher
from careflow_api.events.publisher import Event
from careflow_api.models import ConsentRecordRow
from careflow_api.rls import clinic_session
from careflow_contracts.api_models import ConsentRecord, ConsentRequest
from careflow_contracts.events import ConsentRecordedData

CONSENT_RECORDED: Final = "consent.recorded"


def _record(row: ConsentRecordRow) -> ConsentRecord:
    return ConsentRecord(
        session_id=str(row.session_id),
        camera=row.camera,
        voice=row.voice,
        data_use=row.data_use,
        consent_version=row.consent_version,
        recorded_at=row.recorded_at,
    )


async def record_consent(
    clinic_id: uuid.UUID,
    session_id: str,
    body: ConsentRequest,
    *,
    language: str,
    events: OutboxPublisher,
    now: dt.datetime,
) -> ConsentRecord:
    """Append the choices and emit `consent.recorded`, in one transaction."""
    row = ConsentRecordRow(
        clinic_id=clinic_id,
        session_id=uuid.UUID(session_id),
        camera=body.camera,
        voice=body.voice,
        data_use=body.data_use,
        consent_version=body.consent_version,
        language=language,
        recorded_at=now,
    )
    data = ConsentRecordedData(
        clinic_id=str(clinic_id), session_id=session_id, camera=body.camera, voice=body.voice
    )
    async with clinic_session(clinic_id) as db:
        db.add(row)
        await events.add(db, Event(CONSENT_RECORDED, data, now))
    return _record(row)


async def get_consent(clinic_id: uuid.UUID, session_id: str) -> ConsentRecord | None:
    """The session's latest consent, or None when it has none (nothing is consented)."""
    try:
        key = uuid.UUID(session_id)
    except ValueError:
        return None
    query = (
        select(ConsentRecordRow)
        .where(ConsentRecordRow.session_id == key)
        .order_by(ConsentRecordRow.recorded_at.desc())
        .limit(1)
    )
    async with clinic_session(clinic_id) as db:
        row = (await db.execute(query)).scalar_one_or_none()
    return None if row is None else _record(row)


__all__ = ["CONSENT_RECORDED", "get_consent", "record_consent"]
