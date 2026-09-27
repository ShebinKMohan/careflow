""" "I feel worse" presses (M2-07): recorded first, every one, and summarised for the nurse.

`record_press` stores the press (answer codes and the optional note) and adds the
`patient.feels_worse` event in one transaction, before anything else runs, so the nurse hears of it
even if the rest fails. The event carries the press count and the codes, never the note. A lock
per session numbers concurrent presses; repeats are never dropped or merged.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence

from sqlalchemy import func, select, text

from careflow_api.events.outbox import OutboxPublisher
from careflow_api.events.publisher import Event
from careflow_api.models import FeelsWorseReportRow
from careflow_api.rls import clinic_session
from careflow_contracts.api_models import FeelsWorseSummary
from careflow_contracts.events import EventType, PatientFeelsWorseData


async def record_press(
    clinic_id: uuid.UUID,
    session_id: str,
    *,
    codes: Sequence[str],
    note: str | None,
    events: OutboxPublisher,
    now: dt.datetime,
) -> tuple[str, int]:
    """The press's flag id and number (this press included)."""
    flag_id = f"flg_{uuid.uuid4().hex[:24]}"
    key = uuid.UUID(session_id)
    async with clinic_session(clinic_id) as db:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"careflow.feels-worse:{session_id}"},
        )
        count = (
            await db.execute(
                select(func.count())
                .select_from(FeelsWorseReportRow)
                .where(FeelsWorseReportRow.session_id == key)
            )
        ).scalar_one()
        press = int(count) + 1
        db.add(
            FeelsWorseReportRow(
                clinic_id=clinic_id,
                session_id=key,
                flag_id=flag_id,
                press_number=press,
                answer_codes=list(codes),
                note=note,
                reported_at=now,
            )
        )
        data = PatientFeelsWorseData(
            clinic_id=str(clinic_id),
            session_id=session_id,
            flag_id=flag_id,
            reported_at=now,
            press_count=press,
            danger_answer_codes=list(codes),  # type: ignore[arg-type]
        )
        await events.add(
            db, Event(EventType.patient_feels_worse, data, now, idempotency_key=flag_id)
        )
    return flag_id, press


async def summaries(
    clinic_id: uuid.UUID, session_ids: Sequence[str]
) -> dict[str, FeelsWorseSummary]:
    """Per session: the press count, the latest press's time and answers, and the latest note."""
    keys = [uuid.UUID(sid) for sid in session_ids]
    if not keys:
        return {}
    async with clinic_session(clinic_id) as db:
        rows = (
            await db.execute(
                select(FeelsWorseReportRow)
                .where(FeelsWorseReportRow.session_id.in_(keys))
                .order_by(FeelsWorseReportRow.reported_at.desc())
            )
        ).scalars()
        grouped: dict[str, list[FeelsWorseReportRow]] = {}
        for row in rows:
            grouped.setdefault(str(row.session_id), []).append(row)
    result: dict[str, FeelsWorseSummary] = {}
    for sid, presses in grouped.items():
        latest = presses[0]
        note = next((p.note for p in presses if p.note), None)
        result[sid] = FeelsWorseSummary(
            press_count=len(presses),
            last_reported_at=latest.reported_at,
            danger_answer_codes=list(latest.answer_codes),  # type: ignore[arg-type]
            note=note,
        )
    return result


__all__ = ["record_press", "summaries"]
