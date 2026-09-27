"""Medical questions refused on the phone (M2-09) or asked during the intake (M2-10): stored for
the nurse, announced without words.

`record_medical_question` stores the question in `nurse_flags` and adds `nurse.flag` (reason
`medical_question`) and the internal `rag.refused` (which classifier layer decided) to the outbox,
in one transaction. `record_intake_question` does the same for a question asked mid-intake (reason
`medical_question_intake`), once per session and turn: its flag id and idempotency key come from
the turn, so a node that runs again adds nothing. No event carries the question: the words stay in
the clinic's database for the nurse's worklist. The rows go when the session's token closes or
becomes a no-show (`purge`, in the queue's transaction), or when the session leaves the worklist's
24-hour window without a token (`purge_stale`, from the grace-window loop).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from collections.abc import Iterable, Sequence
from typing import Final

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from careflow_api.events.outbox import OutboxPublisher
from careflow_api.events.publisher import Event
from careflow_api.models import NurseFlagRow, PatientSession
from careflow_api.rls import clinic_session
from careflow_contracts.api_models import MedicalQuestion
from careflow_contracts.events import EventType, NurseFlagData, RagRefusedData
from careflow_contracts.generated.events.nurse_flag_schema import Reason
from careflow_contracts.generated.events.rag_refused_schema import Category
from careflow_rag.agent import MedicalFlag

RAG_REFUSED: Final = "rag.refused"
MAX_QUESTIONS: Final = 20
#: The nurse worklist's window (`routers.nurse.ACTIVE_WINDOW`): older sessions are off the list.
STALE_AFTER: Final = dt.timedelta(hours=24)


async def record_medical_question(
    clinic_id: uuid.UUID, flag: MedicalFlag, *, events: OutboxPublisher, now: dt.datetime
) -> str:
    """The new flag's id, once the question and both events are committed."""
    flag_id = f"flg_{uuid.uuid4().hex[:24]}"
    async with clinic_session(clinic_id) as db:
        db.add(
            NurseFlagRow(
                clinic_id=clinic_id,
                session_id=uuid.UUID(flag.session_id),
                flag_id=flag_id,
                reason=Reason.medical_question.value,
                question_text=flag.question[:500],
                lang=flag.lang,
                category=flag.category,
                raised_at=now,
            )
        )
        nurse = NurseFlagData(
            clinic_id=str(clinic_id),
            session_id=flag.session_id,
            flag_id=flag_id,
            reason=Reason.medical_question,
            raised_at=now,
        )
        await events.add(db, Event(EventType.nurse_flag, nurse, now, idempotency_key=flag_id))
        refused = RagRefusedData(
            clinic_id=str(clinic_id),
            session_id=flag.session_id,
            flag_id=flag_id,
            category=Category(flag.category),
        )
        await events.add(db, Event(RAG_REFUSED, refused, now, idempotency_key=f"{flag_id}:rag"))
    return flag_id


async def record_intake_question(
    clinic_id: uuid.UUID,
    *,
    session_id: str,
    turn_id: str,
    question: str,
    lang: str,
    category: str,
    events: OutboxPublisher,
    now: dt.datetime,
) -> str:
    """The flag of a medical question asked during the intake; the same turn gives the same flag
    and one `nurse.flag` (idempotency key `{session_id}:{turn_id}:nurse_flag`)."""
    key = f"{session_id}:{turn_id}:nurse_flag"
    flag_id = f"flg_{hashlib.sha256(key.encode()).hexdigest()[:24]}"
    async with clinic_session(clinic_id) as db:
        await db.execute(
            insert(NurseFlagRow)
            .values(
                clinic_id=clinic_id,
                session_id=uuid.UUID(session_id),
                flag_id=flag_id,
                reason=Reason.medical_question_intake.value,
                question_text=question[:500],
                lang=lang,
                category=category,
                raised_at=now,
            )
            .on_conflict_do_nothing(index_elements=[NurseFlagRow.flag_id])
        )
        data = NurseFlagData(
            clinic_id=str(clinic_id),
            session_id=session_id,
            flag_id=flag_id,
            reason=Reason.medical_question_intake,
            raised_at=now,
        )
        await events.add(db, Event(EventType.nurse_flag, data, now, idempotency_key=key))
    return flag_id


async def medical_questions(
    clinic_id: uuid.UUID, session_ids: Sequence[str]
) -> dict[str, list[MedicalQuestion]]:
    """Per session: its refused questions, newest first (at most 20)."""
    keys = [uuid.UUID(sid) for sid in session_ids]
    if not keys:
        return {}
    async with clinic_session(clinic_id) as db:
        rows = (
            await db.execute(
                select(NurseFlagRow)
                .where(NurseFlagRow.session_id.in_(keys))
                .order_by(NurseFlagRow.raised_at.desc())
            )
        ).scalars()
        grouped: dict[str, list[MedicalQuestion]] = {}
        for row in rows:
            questions = grouped.setdefault(str(row.session_id), [])
            if len(questions) < MAX_QUESTIONS:
                questions.append(
                    MedicalQuestion(
                        flag_id=row.flag_id,
                        question=row.question_text,
                        language=row.lang,  # type: ignore[arg-type]
                        asked_at=row.raised_at,
                    )
                )
    return grouped


async def purge(db: AsyncSession, session_ids: Iterable[str]) -> None:
    """Delete the flags of sessions that just closed (in the caller's transaction)."""
    keys = [uuid.UUID(sid) for sid in session_ids]
    if keys:
        await db.execute(delete(NurseFlagRow).where(NurseFlagRow.session_id.in_(keys)))


async def purge_stale(db: AsyncSession, now: dt.datetime) -> None:
    """Delete the flags of sessions that left the worklist's window (in the caller's clinic
    scope)."""
    stale = select(PatientSession.id).where(PatientSession.started_at < now - STALE_AFTER)
    await db.execute(delete(NurseFlagRow).where(NurseFlagRow.session_id.in_(stale)))


__all__ = [
    "medical_questions",
    "purge",
    "purge_stale",
    "record_intake_question",
    "record_medical_question",
]
