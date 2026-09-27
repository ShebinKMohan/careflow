"""The queue's rules for one department, without a database (M1-10).

- **Order:** nurse-confirmed level, then time in the queue (`enqueued_at`), then token id.
- **Calling:** `call_next(room)` takes the first waiting token whose level the room accepts;
  `call_next_free()` fills the first free room (config order) that has one. A room is busy while a
  called or arrived token holds it.
- **Grace:** a called token not marked arrived within the grace window is re-queued once, at the
  back of its level, and becomes a no-show on the second miss (`miss`, `expire_grace`).

Time comes from the injected clock; nothing here reads the system time.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from careflow_api.queue.rooms import Department, Room
from careflow_contracts.api_models import QueueTokenStatus as Status

ACTIVE: Final = frozenset({Status.waiting, Status.called, Status.arrived})
HOLDS_ROOM: Final = frozenset({Status.called, Status.arrived})


class InvalidTransition(ValueError):
    """The token is not in a state that allows the action."""


class RoomBusy(ValueError):
    pass


@dataclass
class Token:
    token_id: str
    session_id: str
    token_code: str
    level: int
    issued_at: datetime
    enqueued_at: datetime
    status: Status = Status.waiting
    room_id: str | None = None
    called_at: datetime | None = None
    recall_count: int = 0

    @property
    def order_key(self) -> tuple[int, datetime, str]:
        return (self.level, self.enqueued_at, self.token_id)


class QueueCore:
    def __init__(
        self,
        department: Department,
        *,
        grace_seconds: int,
        clock: Callable[[], datetime],
        tokens: Iterable[Token] = (),
    ) -> None:
        self.department = department
        self.grace = timedelta(seconds=grace_seconds)
        self.clock = clock
        self.tokens = {token.token_id: token for token in tokens}

    def add(self, token: Token) -> Token:
        if token.status is not Status.waiting:
            raise InvalidTransition("a new token starts waiting")
        self.tokens[token.token_id] = token
        return token

    def waiting(self) -> list[Token]:
        return sorted(
            (t for t in self.tokens.values() if t.status is Status.waiting),
            key=lambda t: t.order_key,
        )

    def busy_rooms(self) -> set[str]:
        return {t.room_id for t in self.tokens.values() if t.status in HOLDS_ROOM and t.room_id}

    def free_rooms(self) -> list[Room]:
        busy = self.busy_rooms()
        return [room for room in self.department.rooms if room.id not in busy]

    def call_next(self, room: Room) -> Token | None:
        """Call the first waiting token the room accepts into it, or None."""
        if room.id in self.busy_rooms():
            raise RoomBusy(room.id)
        token = next((t for t in self.waiting() if t.level in room.accepts_levels), None)
        if token is not None:
            token.status, token.room_id, token.called_at = Status.called, room.id, self.clock()
        return token

    def call_next_free(self) -> Token | None:
        """Fill the first free room (config order) that accepts a waiting token."""
        for room in self.free_rooms():
            if (token := self.call_next(room)) is not None:
                return token
        return None

    def _get(self, token_id: str, *allowed: Status) -> Token:
        token = self.tokens[token_id]
        if token.status not in allowed:
            raise InvalidTransition(f"{token.token_code} is {token.status.value}")
        return token

    def mark_arrived(self, token_id: str) -> Token:
        token = self._get(token_id, Status.called)
        token.status = Status.arrived
        return token

    def close(self, token_id: str) -> Token:
        """Seen: the token ends and its room is free."""
        token = self._get(token_id, Status.called, Status.arrived)
        token.status = Status.closed
        return token

    def miss(self, token_id: str) -> Token:
        """A called patient did not come: re-queue once at the back of the level, then no-show."""
        token = self._get(token_id, Status.called)
        if token.recall_count == 0:
            token.status, token.recall_count = Status.waiting, 1
            token.enqueued_at, token.room_id, token.called_at = self.clock(), None, None
        else:
            token.status = Status.no_show
        return token

    def grace_until(self, token: Token) -> datetime | None:
        if token.status is not Status.called or token.called_at is None:
            return None
        return token.called_at + self.grace

    def expire_grace(self) -> list[Token]:
        """Apply `miss` to every called token whose grace window has ended, oldest call first."""
        now = self.clock()
        due = [
            t
            for t in self.tokens.values()
            if (until := self.grace_until(t)) is not None and until <= now
        ]
        return [self.miss(t.token_id) for t in sorted(due, key=lambda t: (t.called_at, t.token_id))]


__all__ = ["ACTIVE", "HOLDS_ROOM", "InvalidTransition", "QueueCore", "RoomBusy", "Status", "Token"]
