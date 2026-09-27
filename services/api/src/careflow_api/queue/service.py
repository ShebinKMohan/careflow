"""`QueueService`: the queue on Postgres (M1-10).

Every change to a department's queue runs in the caller's transaction, which RLS scopes to the
caller's clinic, under a transaction-level advisory lock for that clinic and department. It loads
the department's active tokens into `QueueCore`, applies the rule and writes the changed tokens
back. Events (`token.called`, `token.no_show`) go through `EventPublisher.add` in the same
transaction, so they exist exactly when the change commits. A token that closes or becomes a
no-show also deletes its session's refused medical questions (`nurse_flags`, M2-09).

Token codes are `<prefix>-<NNN>`, numbered per clinic, department and local day from a counter row
locked with `SELECT ... FOR UPDATE`. After 999 the number wraps to 1, skipping any code an active
token still holds.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from careflow_api import nurse_flags
from careflow_api.db import get_sessionmaker
from careflow_api.events.publisher import Event, EventPublisher
from careflow_api.models import Clinic, PatientSession, QueueTokenRow, TokenCounter
from careflow_api.queue.core import ACTIVE, QueueCore, Status, Token
from careflow_api.queue.rooms import Department, RoomsConfig
from careflow_api.queue.wait import estimate_wait
from careflow_api.rls import clinic_session
from careflow_contracts.api_models import BoardEntry, CompanionStatus, QueueToken, SessionState
from careflow_contracts.events import EventType, TokenCalledData, TokenNoShowData

logger = logging.getLogger("careflow_api.queue")

MAX_NUMBER: Final = 999
GRACE_INTERVAL_SECONDS: Final = 10.0
ACTIVE_VALUES: Final = tuple(s.value for s in ACTIVE)


def utc_now() -> datetime:
    return datetime.now(UTC)


class UnknownToken(LookupError):
    pass


class UnknownSession(LookupError):
    pass


class QueueFull(RuntimeError):
    """Every code of the department is held by an active token."""


@dataclass(frozen=True)
class TokenAssignment:
    token_id: str
    token_code: str
    department: str
    #: Where to wait: the department's name (the room comes with the call).
    room_hint: str
    #: An estimate only (`queue.wait.estimate_wait`).
    wait_estimate_min: int


def to_token(row: QueueTokenRow) -> Token:
    return Token(
        token_id=str(row.id),
        session_id=str(row.session_id),
        token_code=row.token_code,
        level=row.level,
        issued_at=row.issued_at,
        enqueued_at=row.enqueued_at,
        status=Status(row.status),
        room_id=row.room_id,
        called_at=row.called_at,
        recall_count=row.recall_count,
    )


#: Sends each session its place (`queue.status`, M2-07): `(clinic_id, [(session_id, status)])`.
type Notify = Callable[[uuid.UUID, list[tuple[str, CompanionStatus]]], Awaitable[None]]

#: A token's status as the patient's session state.
TOKEN_STATES: Final = {
    Status.waiting: SessionState.queued,
    Status.called: SessionState.called,
    Status.arrived: SessionState.arrived,
    Status.no_show: SessionState.no_show,
    Status.closed: SessionState.closed,
}


class QueueService:
    def __init__(
        self,
        rooms: RoomsConfig,
        events: EventPublisher,
        *,
        clock: Callable[[], datetime] = utc_now,
        notify: Notify | None = None,
    ) -> None:
        self.rooms, self.events, self.clock = rooms, events, clock
        self.notify = notify

    # -- helpers ---------------------------------------------------------------------------

    async def _lock(self, db: AsyncSession, clinic_id: uuid.UUID, department: str) -> None:
        key = f"careflow.queue:{clinic_id}:{department}"
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
        )

    async def _core(self, db: AsyncSession, department: Department) -> QueueCore:
        rows = (
            await db.execute(
                select(QueueTokenRow).where(
                    QueueTokenRow.department == department.id,
                    QueueTokenRow.status.in_(ACTIVE_VALUES),
                )
            )
        ).scalars()
        return QueueCore(
            department,
            grace_seconds=self.rooms.grace_seconds,
            clock=self.clock,
            tokens=[to_token(row) for row in rows],
        )

    async def _write(self, db: AsyncSession, tokens: Iterable[Token]) -> None:
        tokens = list(tokens)
        # A closed or no-show session's refused medical questions go with it (M2-09).
        await nurse_flags.purge(
            db, [t.session_id for t in tokens if t.status in (Status.closed, Status.no_show)]
        )
        for token in tokens:
            await db.execute(
                update(QueueTokenRow)
                .where(QueueTokenRow.id == uuid.UUID(token.token_id))
                .values(
                    status=token.status.value,
                    room_id=token.room_id,
                    called_at=token.called_at,
                    enqueued_at=token.enqueued_at,
                    recall_count=token.recall_count,
                    updated_at=self.clock(),
                )
            )

    async def _next_code(
        self, db: AsyncSession, clinic_id: uuid.UUID, department: Department
    ) -> str:
        zone = (
            await db.execute(select(Clinic.timezone).where(Clinic.id == clinic_id))
        ).scalar_one()
        day = self.clock().astimezone(ZoneInfo(zone)).date()
        key = {"clinic_id": clinic_id, "department": department.id, "day": day}
        await db.execute(insert(TokenCounter).values(**key, last_number=0).on_conflict_do_nothing())
        counter = (
            await db.execute(select(TokenCounter).filter_by(**key).with_for_update())
        ).scalar_one()
        held = set(
            (
                await db.execute(
                    select(QueueTokenRow.token_code).where(
                        QueueTokenRow.department == department.id,
                        QueueTokenRow.status.in_(ACTIVE_VALUES),
                    )
                )
            ).scalars()
        )
        number = counter.last_number
        for _ in range(MAX_NUMBER):
            number = number % MAX_NUMBER + 1
            code = f"{department.prefix}-{number:03d}"
            if code not in held:
                counter.last_number = number
                return code
        raise QueueFull(department.id)

    async def _department_of(self, db: AsyncSession, token_id: str) -> Department:
        try:
            key = uuid.UUID(token_id)
        except ValueError as exc:
            raise UnknownToken(token_id) from exc
        found = (
            await db.execute(select(QueueTokenRow.department).where(QueueTokenRow.id == key))
        ).scalar_one_or_none()
        if found is None:
            raise UnknownToken(token_id)
        return self.rooms.department(found)

    async def _change(
        self,
        db: AsyncSession,
        clinic_id: uuid.UUID,
        token_id: str,
        action: Callable[[QueueCore, str], Token],
    ) -> tuple[Token, Department]:
        department = await self._department_of(db, token_id)
        await self._lock(db, clinic_id, department.id)
        core = await self._core(db, department)
        if token_id not in core.tokens:  # no longer active: report its state as a conflict
            row = await db.get(QueueTokenRow, uuid.UUID(token_id))
            if row is None:
                raise UnknownToken(token_id)
            core.tokens[token_id] = to_token(row)
        token = action(core, token_id)
        await self._write(db, [token])
        if token.status is Status.no_show:
            await self._no_show(db, clinic_id, department, token)
        return token, department

    async def _no_show(
        self, db: AsyncSession, clinic_id: uuid.UUID, department: Department, token: Token
    ) -> None:
        data = TokenNoShowData(
            clinic_id=str(clinic_id),
            session_id=token.session_id,
            token_id=token.token_id,
            token_code=token.token_code,
            department=department.id,
            marked_at=self.clock(),
        )
        await self.events.add(db, Event(EventType.token_no_show, data, self.clock()))

    def room_name(self, department: Department, room_id: str | None) -> str | None:
        return None if room_id is None else department.room(room_id).name

    def to_api(self, token: Token, department: Department) -> QueueToken:
        grace = timedelta(seconds=self.rooms.grace_seconds)
        called = token.status is Status.called and token.called_at is not None
        return QueueToken(
            token_id=token.token_id,
            session_id=token.session_id,
            token_code=token.token_code,
            department=department.id,
            room=self.room_name(department, token.room_id),
            status=token.status,
            issued_at=token.issued_at,
            called_at=token.called_at,
            grace_until=token.called_at + grace if called and token.called_at else None,
        )

    # -- operations --------------------------------------------------------------------------

    async def assign_token(
        self,
        db: AsyncSession,
        *,
        clinic_id: uuid.UUID,
        session_id: str,
        level: int,
        department: str | None = None,
    ) -> TokenAssignment:
        """Queue a session at its nurse-confirmed level (call only after the nurse's decision;
        safety rule 3). A session already holding an active token gets that token back."""
        target = (
            self.rooms.department(department) if department else self.rooms.department_for(level)
        )
        await self._lock(db, clinic_id, target.id)
        session_key = uuid.UUID(session_id)
        row = (
            await db.execute(
                select(QueueTokenRow).where(
                    QueueTokenRow.session_id == session_key,
                    QueueTokenRow.status.in_(ACTIVE_VALUES),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            arrived = (
                await db.execute(
                    select(PatientSession.started_at).where(PatientSession.id == session_key)
                )
            ).scalar_one_or_none()
            if arrived is None:
                raise UnknownSession(session_id)
            now = self.clock()
            row = QueueTokenRow(
                clinic_id=clinic_id,
                department=target.id,
                token_code=await self._next_code(db, clinic_id, target),
                session_id=session_key,
                level=level,
                arrived_at=arrived,
                issued_at=now,
                enqueued_at=arrived,
                status=Status.waiting.value,
                recall_count=0,
            )
            db.add(row)
            await db.flush()
        department_of_row = self.rooms.department(row.department)
        core = await self._core(db, department_of_row)
        token = core.tokens[str(row.id)]
        return TokenAssignment(
            token_id=token.token_id,
            token_code=token.token_code,
            department=department_of_row.id,
            room_hint=department_of_row.name,
            wait_estimate_min=estimate_wait(token, core, self.rooms.service_minutes),
        )

    async def call_next(
        self,
        db: AsyncSession,
        *,
        clinic_id: uuid.UUID,
        department: str,
        room: str | None = None,
    ) -> QueueToken | None:
        """Call the next token into `room`, or into the first free room with an eligible token;
        publishes `token.called`. None when nobody waiting fits a free room."""
        target = self.rooms.department(department)
        chosen = target.room(room) if room is not None else None
        await self._lock(db, clinic_id, target.id)
        core = await self._core(db, target)
        token = core.call_next(chosen) if chosen is not None else core.call_next_free()
        if token is None:
            return None
        await self._write(db, [token])
        if token.room_id is None or token.called_at is None:
            raise RuntimeError("a called token has a room and a call time")
        data = TokenCalledData(
            clinic_id=str(clinic_id),
            session_id=token.session_id,
            token_id=token.token_id,
            token_code=token.token_code,
            department=target.id,
            room=target.room(token.room_id).name,
            called_at=token.called_at,
        )
        await self.events.add(db, Event(EventType.token_called, data, self.clock()))
        return self.to_api(token, target)

    async def mark_arrived(
        self, db: AsyncSession, *, clinic_id: uuid.UUID, token_id: str
    ) -> QueueToken:
        token, department = await self._change(
            db, clinic_id, token_id, lambda core, tid: core.mark_arrived(tid)
        )
        return self.to_api(token, department)

    async def close(self, db: AsyncSession, *, clinic_id: uuid.UUID, token_id: str) -> QueueToken:
        token, department = await self._change(
            db, clinic_id, token_id, lambda core, tid: core.close(tid)
        )
        return self.to_api(token, department)

    async def mark_missed(
        self, db: AsyncSession, *, clinic_id: uuid.UUID, token_id: str
    ) -> QueueToken:
        """The nurse says the called patient did not come: the grace rule, applied now."""
        token, department = await self._change(
            db, clinic_id, token_id, lambda core, tid: core.miss(tid)
        )
        return self.to_api(token, department)

    async def expire_grace(self, db: AsyncSession, *, clinic_id: uuid.UUID) -> list[QueueToken]:
        """Re-queue or mark no-show every called token past its grace window, in every
        department of the clinic."""
        changed: list[QueueToken] = []
        for department in self.rooms.departments:
            await self._lock(db, clinic_id, department.id)
            core = await self._core(db, department)
            missed = core.expire_grace()
            await self._write(db, missed)
            for token in missed:
                if token.status is Status.no_show:
                    await self._no_show(db, clinic_id, department, token)
                changed.append(self.to_api(token, department))
        return changed

    async def board(self, db: AsyncSession, *, department: str) -> list[BoardEntry]:
        """What a public board may show: called tokens with their room (latest call first), then
        waiting tokens in queue order. Never a name or a level."""
        target = self.rooms.department(department)
        core = await self._core(db, target)
        called = sorted(
            (t for t in core.tokens.values() if t.status is Status.called),
            key=lambda t: (t.called_at or t.issued_at, t.token_id),
            reverse=True,
        )
        return [
            BoardEntry(
                token_code=t.token_code,
                room=self.room_name(target, t.room_id),
                status=t.status,
            )
            for t in [*called, *core.waiting()]
        ]

    def status_of(self, token: Token, core: QueueCore, department: Department) -> CompanionStatus:
        """What the patient's phone shows for a token (M2-07): never a level."""
        waiting = token.status is Status.waiting
        order = [t.token_id for t in core.waiting()]
        return CompanionStatus(
            state=TOKEN_STATES.get(token.status, SessionState.queued),
            position=order.index(token.token_id) + 1
            if waiting and token.token_id in order
            else None,
            wait_estimate_minutes=(
                estimate_wait(token, core, self.rooms.service_minutes) if waiting else None
            ),
            token=self.to_api(token, department),
            room=self.room_name(department, token.room_id),
        )

    async def companion_status(
        self, db: AsyncSession, *, session_id: str, session_state: str
    ) -> CompanionStatus:
        """The waiting room's status for one session: its place when it holds an active token,
        else its session state with no position."""
        token = await self.session_token(db, session_id=session_id)
        if token is not None and token.status.value in ACTIVE_VALUES:
            department = self.rooms.department(token.department)
            core = await self._core(db, department)
            found = core.tokens.get(token.token_id)
            if found is not None:
                return self.status_of(found, core, department)
        try:
            state = SessionState(session_state)
        except ValueError:
            state = SessionState.intake
        if token is not None:
            state = TOKEN_STATES.get(Status(token.status.value), state)
        return CompanionStatus(
            state=state,
            position=None,
            wait_estimate_minutes=None,
            token=token,
            room=token.room if token is not None else None,
        )

    async def broadcast(self, clinic_id: uuid.UUID, department: str) -> None:
        """Send every waiting or called session of the department its place (`queue.status`),
        after a change has committed. Best effort: the phone also polls."""
        if self.notify is None:
            return
        try:
            target = self.rooms.department(department)
            async with clinic_session(clinic_id) as db:
                core = await self._core(db, target)
            statuses = [
                (token.session_id, self.status_of(token, core, target))
                for token in core.tokens.values()
                if token.status in (Status.waiting, Status.called)
            ]
            await self.notify(clinic_id, statuses)
        except Exception:
            logger.exception("queue.broadcast_failed")

    async def tell(self, clinic_id: uuid.UUID, token: QueueToken) -> None:
        """Send the changed token's own phone its state (`queue.status`) after a nurse marks it
        arrived, a no-show or seen. `broadcast` covers only waiting and called tokens, so without
        this a closed visit's phone would keep its last state (M2-01, the final state). Best
        effort, after the commit: the phone also re-reads its status when it reconnects."""
        if self.notify is None:
            return
        try:
            async with clinic_session(clinic_id) as db:
                status = await self.companion_status(
                    db, session_id=token.session_id, session_state=SessionState.queued.value
                )
            await self.notify(clinic_id, [(token.session_id, status)])
        except Exception:
            logger.exception("queue.tell_failed")

    async def session_token(self, db: AsyncSession, *, session_id: str) -> QueueToken | None:
        """The session's active token, or its latest one."""
        return (await self.session_tokens(db, session_ids=[session_id])).get(session_id)

    async def session_tokens(
        self, db: AsyncSession, *, session_ids: Sequence[str]
    ) -> dict[str, QueueToken]:
        """`session_token` for many sessions in one query (the nurse's worklist, SHE-325): each
        session's newest active token, else its newest one. Sessions without a token are left
        out."""
        keys: list[uuid.UUID] = []
        for session_id in session_ids:
            try:
                keys.append(uuid.UUID(session_id))
            except ValueError:
                continue
        if not keys:
            return {}
        found = (
            await db.execute(
                select(QueueTokenRow)
                .where(QueueTokenRow.session_id.in_(keys))
                .order_by(QueueTokenRow.issued_at.desc())
            )
        ).scalars()
        chosen: dict[str, QueueTokenRow] = {}
        for row in found:  # newest first: keep the first, unless a later one is active
            session_id = str(row.session_id)
            best = chosen.get(session_id)
            if best is None or (best.status not in ACTIVE_VALUES and row.status in ACTIVE_VALUES):
                chosen[session_id] = row
        return {
            session_id: self.to_api(to_token(row), self.rooms.department(row.department))
            for session_id, row in chosen.items()
        }


async def run_grace_expiry(
    service: QueueService, *, interval: float = GRACE_INTERVAL_SECONDS
) -> None:
    """Every `interval` seconds, apply the grace window in every clinic (the API lifespan)."""
    while True:
        try:
            async with get_sessionmaker()() as db:
                clinic_ids = list((await db.execute(select(Clinic.id))).scalars())
            for clinic_id in clinic_ids:
                async with clinic_session(clinic_id) as db:
                    changed = await service.expire_grace(db, clinic_id=clinic_id)
                    await nurse_flags.purge_stale(db, service.clock())
                for department in {token.department for token in changed}:
                    await service.broadcast(clinic_id, department)
        except Exception:  # keep running: the next tick retries
            logger.exception("queue.grace_expiry_failed")
        await asyncio.sleep(interval)


__all__ = [
    "GRACE_INTERVAL_SECONDS",
    "QueueFull",
    "QueueService",
    "TokenAssignment",
    "UnknownSession",
    "UnknownToken",
    "run_grace_expiry",
    "utc_now",
]
