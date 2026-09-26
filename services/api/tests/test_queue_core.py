"""QueueCore, the rooms config and the wait estimate (M1-10): pure, no database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from careflow_api.queue.core import InvalidTransition, QueueCore, RoomBusy, Status, Token
from careflow_api.queue.rooms import Department, Room, RoomsConfig, UnknownRoom, load_rooms, rooms
from careflow_api.queue.wait import estimate_wait

T0 = datetime(2026, 9, 26, 9, 0, tzinfo=UTC)
ED = Department(
    id="emergency",
    name="Emergency",
    prefix="A",
    rooms=(
        Room(id="resus-1", name="Resus 1", accepts_levels=frozenset({1, 2})),
        Room(id="ed-1", name="ED 1", accepts_levels=frozenset({1, 2, 3, 4, 5})),
        Room(id="ed-2", name="ED 2", accepts_levels=frozenset({1, 2, 3, 4, 5})),
    ),
)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def token(n: int, level: int, minute: int) -> Token:
    at = T0 + timedelta(minutes=minute)
    return Token(f"t{n}", f"s{n}", f"A-{n:03d}", level, issued_at=at, enqueued_at=at)


def queue(*tokens: Token, clock: Clock | None = None) -> QueueCore:
    return QueueCore(ED, grace_seconds=120, clock=clock or Clock(), tokens=tokens)


def test_queue_order_is_level_then_time_in_queue() -> None:
    q = queue(token(1, 3, 0), token(2, 2, 5), token(3, 3, -5), token(4, 1, 10), token(5, 2, 1))
    assert [t.token_id for t in q.waiting()] == ["t4", "t5", "t2", "t3", "t1"]


def test_call_next_free_puts_the_most_urgent_token_in_the_first_free_room() -> None:
    q = queue(token(1, 3, 0), token(2, 2, 5), token(3, 4, 1))
    first = q.call_next_free()
    assert first is not None and (first.token_id, first.room_id) == ("t2", "resus-1")
    second = q.call_next_free()  # resus is busy; a level 3 goes to ED 1
    assert second is not None and (second.token_id, second.room_id) == ("t1", "ed-1")
    third = q.call_next_free()
    assert third is not None and (third.token_id, third.room_id) == ("t3", "ed-2")
    assert q.call_next_free() is None and q.free_rooms() == []


def test_a_room_only_takes_the_levels_it_accepts() -> None:
    q = queue(token(1, 3, 0), token(2, 5, 1))
    resus = ED.room("resus-1")
    assert q.call_next(resus) is None  # levels 3 and 5 never go to resus
    assert all(t.status is Status.waiting for t in q.tokens.values())
    called = q.call_next_free()
    assert called is not None and (called.token_id, called.room_id) == ("t1", "ed-1")
    with pytest.raises(RoomBusy):
        q.call_next(ED.room("ed-1"))
    with pytest.raises(UnknownRoom):
        ED.room("nope")


def test_a_missed_call_is_requeued_once_then_becomes_a_no_show() -> None:
    clock = Clock()
    q = queue(token(1, 3, 0), token(2, 3, 1), clock=clock)
    called = q.call_next_free()
    assert called is not None and called.token_id == "t1"
    clock.advance(119)
    assert q.expire_grace() == []  # still inside the 120 s window
    clock.advance(1)
    [missed] = q.expire_grace()
    assert (missed.status, missed.recall_count, missed.room_id, missed.called_at) == (
        Status.waiting,
        1,
        None,
        None,
    )
    assert missed.enqueued_at == clock.now  # back of its level: behind t2 now
    assert [t.token_id for t in q.waiting()] == ["t2", "t1"]
    q.call_next_free()  # t2
    again = q.call_next_free()
    assert again is not None and again.token_id == "t1"
    clock.advance(120)
    missed_twice = q.expire_grace()
    # Both were called at the same moment; the oldest call, then the token id, goes first.
    assert [(t.token_id, t.status, t.recall_count) for t in missed_twice] == [
        ("t1", Status.no_show, 1),
        ("t2", Status.waiting, 1),
    ]
    assert "ed-2" not in q.busy_rooms()  # a no-show frees its room


def test_arriving_in_time_stops_the_grace_clock_and_close_frees_the_room() -> None:
    clock = Clock()
    q = queue(token(1, 3, 0), clock=clock)
    called = q.call_next_free()
    assert called is not None and q.grace_until(called) == T0 + timedelta(seconds=120)
    q.mark_arrived("t1")
    clock.advance(600)
    assert q.expire_grace() == [] and q.tokens["t1"].status is Status.arrived
    assert q.busy_rooms() == {"ed-1"}
    q.close("t1")
    assert q.busy_rooms() == set() and q.tokens["t1"].status is Status.closed


def test_invalid_transitions_are_refused() -> None:
    q = queue(token(1, 3, 0))
    for action in (q.mark_arrived, q.miss, q.close):
        with pytest.raises(InvalidTransition):
            action("t1")  # waiting: not called yet
    with pytest.raises(InvalidTransition):
        q.add(Token("t9", "s9", "A-009", 3, T0, T0, status=Status.called))


def test_the_wait_estimate_sums_the_tokens_ahead_over_the_rooms_that_fit() -> None:
    minutes = {1: 30.0, 2: 25.0, 3: 20.0, 4: 12.0, 5: 8.0}
    q = queue(token(1, 2, 0), token(2, 3, 1), token(3, 3, 2), token(4, 4, 3), token(5, 5, 4))
    # Level 3 at minute 2: ahead are t1 (25) and t2 (20) = 45 min over 2 rooms (ED 1, ED 2):
    # 22.5 -> 25 in 5-minute buckets.
    assert estimate_wait(q.tokens["t3"], q, minutes) == 25
    # Level 2 at minute 0: nobody ahead.
    assert estimate_wait(q.tokens["t1"], q, minutes) == 0
    # Level 5: 25 + 20 + 20 + 12 = 77 over 2 rooms = 38.5 -> 40.
    assert estimate_wait(q.tokens["t5"], q, minutes) == 40


def test_the_rooms_file_is_valid_and_routes_every_level() -> None:
    config = rooms()
    assert {d.id: d.prefix for d in config.departments} == {"emergency": "A", "general": "G"}
    assert config.grace_seconds == 120
    for level in range(1, 6):
        department = config.department_for(level)
        assert any(level in room.accepts_levels for room in department.rooms)
    assert all(len(room.name) <= 32 for d in config.departments for room in d.rooms)


def department_body(department_id: str, prefix: str, levels: list[int]) -> dict[str, object]:
    room = {"id": f"{department_id}-1", "name": "Room 1", "accepts_levels": levels}
    return {"id": department_id, "name": department_id, "prefix": prefix, "rooms": [room]}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"routing": {1: "nowhere", 2: "ed", 3: "ed", 4: "gp", 5: "gp"}}, "unknown department"),
        (
            {
                "departments": [
                    department_body("ed", "A", [1, 2, 3]),
                    department_body("gp", "G", [4]),
                ]
            },
            "no room in gp accepts level 5",
        ),
        ({"service_minutes": {1: 30, 2: 25}}, "every level"),
        (
            {
                "departments": [
                    department_body("ed", "A", [1, 2, 3]),
                    department_body("gp", "A", [4, 5]),
                ]
            },
            "unique",
        ),
    ],
)
def test_a_bad_rooms_file_is_refused(
    tmp_path: Path, change: dict[str, object], message: str
) -> None:
    body: dict[str, object] = {
        "grace_seconds": 120,
        "service_minutes": {1: 30, 2: 25, 3: 20, 4: 12, 5: 8},
        "routing": {1: "ed", 2: "ed", 3: "ed", 4: "gp", 5: "gp"},
        "departments": [department_body("ed", "A", [1, 2, 3]), department_body("gp", "G", [4, 5])],
    }
    RoomsConfig.model_validate(body)  # the baseline is valid
    with pytest.raises(ValidationError, match=message):
        RoomsConfig.model_validate(body | change)
    path = tmp_path / "rooms.yaml"
    path.write_text("grace_seconds: 0\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_rooms(path)
