"""QueueService (M1-10) on the careflow_test database: numbering, calling, grace, events and RLS.

Needs Postgres (`make up`); skipped without it. Tokens live in this module's own clinics, so the
shared clinics' row counts stay as test_rls expects.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import Engine

from careflow_api.events.publisher import MemoryEventPublisher
from careflow_api.queue.core import InvalidTransition, RoomBusy
from careflow_api.queue.rooms import rooms
from careflow_api.queue.service import QueueService, UnknownToken
from careflow_api.rls import clinic_session
from careflow_contracts.api_models import QueueTokenStatus as Status
from careflow_contracts.events import EventType

T0 = datetime(2026, 9, 26, 9, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0 + timedelta(hours=1)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clinic(database: Engine) -> uuid.UUID:
    """A fresh clinic per test, so numbering and queues start empty."""
    clinic_id = uuid.uuid4()
    with database.begin() as connection:
        connection.execute(
            text("INSERT INTO clinic (id, name, timezone) VALUES (:id, :name, 'Asia/Dubai')"),
            {"id": clinic_id, "name": f"queue-{clinic_id.hex[:8]}"},
        )
    return clinic_id


def sessions(database: Engine, clinic_id: uuid.UUID, count: int) -> list[str]:
    """`count` patient sessions that arrived one minute apart from T0."""
    ids = [uuid.uuid4() for _ in range(count)]
    with database.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.clinic_id', :c, true)"), {"c": str(clinic_id)}
        )
        for i, session_id in enumerate(ids):
            connection.execute(
                text(
                    "INSERT INTO patient_session (id, clinic_id, language, started_at, expires_at) "
                    "VALUES (:id, :c, 'en', :at, :at + interval '2 hours')"
                ),
                {"id": session_id, "c": clinic_id, "at": T0 + timedelta(minutes=i)},
            )
    return [str(i) for i in ids]


class Setup:
    def __init__(self) -> None:
        self.clock = Clock()
        self.events = MemoryEventPublisher()
        self.service = QueueService(rooms(), self.events, clock=self.clock)

    async def assign(self, clinic_id: uuid.UUID, session_id: str, level: int) -> str:
        async with clinic_session(clinic_id) as db:
            return (
                await self.service.assign_token(
                    db, clinic_id=clinic_id, session_id=session_id, level=level
                )
            ).token_code

    async def call(
        self, clinic_id: uuid.UUID, department: str, room: str | None = None
    ) -> tuple[str, str | None] | None:
        async with clinic_session(clinic_id) as db:
            token = await self.service.call_next(
                db, clinic_id=clinic_id, department=department, room=room
            )
        return None if token is None else (token.token_code, token.room)

    def called(self) -> list[tuple[str, str]]:
        return [
            (e.data.model_dump()["token_code"], e.data.model_dump()["room"])
            for e in self.events.events
            if e.type is EventType.token_called
        ]


async def test_tokens_are_numbered_per_department_and_called_by_level_then_arrival(
    app: FastAPI, database: Engine, clinic: uuid.UUID
) -> None:
    s = Setup()
    s1, s2, s3, s4 = sessions(database, clinic, 4)
    assert [
        await s.assign(clinic, s1, 3),
        await s.assign(clinic, s2, 4),  # levels 4-5 queue in General
        await s.assign(clinic, s3, 2),
        await s.assign(clinic, s4, 3),
    ] == ["A-001", "G-001", "A-002", "A-003"]
    assert await s.call(clinic, "emergency") == ("A-002", "Resus 1")  # level 2 first
    assert await s.call(clinic, "emergency") == ("A-001", "ED 1")  # then level 3 by arrival
    assert await s.call(clinic, "emergency") == ("A-003", "ED 2")
    assert await s.call(clinic, "emergency") is None
    assert await s.call(clinic, "general") == ("G-001", "Room 1")
    assert s.called() == [
        ("A-002", "Resus 1"),
        ("A-001", "ED 1"),
        ("A-003", "ED 2"),
        ("G-001", "Room 1"),
    ]
    async with clinic_session(clinic) as db:  # a session keeps its active token
        again = await s.service.assign_token(db, clinic_id=clinic, session_id=s1, level=3)
    assert again.token_code == "A-001"


async def test_20_concurrent_assignments_get_unique_codes(
    app: FastAPI, database: Engine, clinic: uuid.UUID
) -> None:
    s = Setup()
    codes = await asyncio.gather(
        *(s.assign(clinic, sid, 3) for sid in sessions(database, clinic, 20))
    )
    assert sorted(codes) == [f"A-{n:03d}" for n in range(1, 21)]


async def test_a_room_only_takes_the_levels_it_accepts(
    app: FastAPI, database: Engine, clinic: uuid.UUID
) -> None:
    s = Setup()
    [sid] = sessions(database, clinic, 1)
    await s.assign(clinic, sid, 3)
    assert await s.call(clinic, "emergency", room="resus-1") is None  # resus takes levels 1-2
    assert await s.call(clinic, "emergency", room="ed-3") == ("A-001", "ED 3")
    with pytest.raises(RoomBusy):
        await s.call(clinic, "emergency", room="ed-3")


async def test_a_missed_call_is_requeued_once_then_a_no_show(
    app: FastAPI, database: Engine, clinic: uuid.UUID
) -> None:
    s = Setup()
    [sid] = sessions(database, clinic, 1)
    await s.assign(clinic, sid, 3)
    await s.call(clinic, "emergency")
    s.clock.advance(119)
    async with clinic_session(clinic) as db:
        assert await s.service.expire_grace(db, clinic_id=clinic) == []
    s.clock.advance(2)
    async with clinic_session(clinic) as db:
        [first] = await s.service.expire_grace(db, clinic_id=clinic)
    assert (first.status, first.room) == (Status.waiting, None)
    await s.call(clinic, "emergency")
    s.clock.advance(121)
    async with clinic_session(clinic) as db:
        [second] = await s.service.expire_grace(db, clinic_id=clinic)
    assert second.status is Status.no_show
    assert [e.type for e in s.events.events] == [
        EventType.token_called,
        EventType.token_called,
        EventType.token_no_show,
    ]
    with database.begin() as connection:
        connection.execute(text("SELECT set_config('app.clinic_id', :c, true)"), {"c": str(clinic)})
        row = connection.execute(text("SELECT status, recall_count FROM queue_tokens")).one()
    assert tuple(row) == ("no_show", 1)


async def test_nurse_actions_follow_the_token_life(
    app: FastAPI, database: Engine, clinic: uuid.UUID
) -> None:
    s = Setup()
    sid, other = sessions(database, clinic, 2)
    await s.assign(clinic, sid, 3)
    await s.assign(clinic, other, 3)
    async with clinic_session(clinic) as db:
        called = await s.service.call_next(db, clinic_id=clinic, department="emergency")
        assert called is not None and called.grace_until == s.clock.now + timedelta(seconds=120)
        missed = await s.service.mark_missed(db, clinic_id=clinic, token_id=called.token_id)
        assert (missed.status, missed.called_at) == (Status.waiting, None)
        again = await s.service.call_next(db, clinic_id=clinic, department="emergency")
        assert again is not None and again.token_code == "A-002"  # the re-queued token went back
        arrived = await s.service.mark_arrived(db, clinic_id=clinic, token_id=again.token_id)
        assert arrived.status is Status.arrived and arrived.grace_until is None
        closed = await s.service.close(db, clinic_id=clinic, token_id=again.token_id)
        assert closed.status is Status.closed
        with pytest.raises(InvalidTransition):
            await s.service.mark_arrived(db, clinic_id=clinic, token_id=again.token_id)
        with pytest.raises(InvalidTransition):
            await s.service.close(db, clinic_id=clinic, token_id=missed.token_id)  # waiting
        with pytest.raises(UnknownToken):
            await s.service.close(db, clinic_id=clinic, token_id=str(uuid.uuid4()))
        with pytest.raises(UnknownToken):
            await s.service.close(db, clinic_id=clinic, token_id="not-a-uuid")


async def test_the_wait_estimate_counts_the_tokens_ahead(
    app: FastAPI, database: Engine, clinic: uuid.UUID
) -> None:
    s = Setup()
    ids = sessions(database, clinic, 3)
    estimates = []
    for sid in ids:
        async with clinic_session(clinic) as db:
            assignment = await s.service.assign_token(db, clinic_id=clinic, session_id=sid, level=3)
        estimates.append(assignment.wait_estimate_min)
    # Level 3 fits ED 1-3: 0, then 20/3 = 6.7 -> 10, then 40/3 = 13.3 -> 15 minutes.
    assert estimates == [0, 10, 15]


async def test_numbering_wraps_after_999_and_skips_active_codes(
    app: FastAPI, database: Engine, clinic: uuid.UUID
) -> None:
    s = Setup()
    first, second = sessions(database, clinic, 2)

    def set_counter(value: int) -> None:
        day = s.clock.now.astimezone(ZoneInfo("Asia/Dubai")).date()
        with database.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.clinic_id', :c, true)"), {"c": str(clinic)}
            )
            connection.execute(
                text(
                    "INSERT INTO token_counters (clinic_id, department, day, last_number) "
                    "VALUES (:c, 'emergency', :d, :n) ON CONFLICT (clinic_id, department, day) "
                    "DO UPDATE SET last_number = :n"
                ),
                {"c": clinic, "d": day, "n": value},
            )

    set_counter(998)
    assert await s.assign(clinic, first, 3) == "A-999"
    set_counter(998)
    assert await s.assign(clinic, second, 3) == "A-001"  # A-999 is still waiting: skipped


async def test_another_clinic_sees_none_of_the_queue(
    app: FastAPI, database: Engine, clinic: uuid.UUID
) -> None:
    s = Setup()
    [sid] = sessions(database, clinic, 1)
    await s.assign(clinic, sid, 3)
    other = uuid.uuid4()
    with database.begin() as connection:
        connection.execute(
            text("INSERT INTO clinic (id, name, timezone) VALUES (:id, :name, 'Asia/Dubai')"),
            {"id": other, "name": f"queue-{other.hex[:8]}"},
        )
    async with clinic_session(other) as db:
        assert await s.service.board(db, department="emergency") == []
        assert await s.service.session_token(db, session_id=sid) is None
        assert await s.service.call_next(db, clinic_id=other, department="emergency") is None
        count = (await db.execute(text("SELECT count(*) FROM queue_tokens"))).scalar_one()
    assert count == 0
    async with clinic_session(clinic) as db:
        assert [e.token_code for e in await s.service.board(db, department="emergency")] == [
            "A-001"
        ]
