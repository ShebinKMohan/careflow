"""`EventPublisher.add(db, event)`: publish an event inside the caller's DB transaction.

M1-11 implements it with a transactional outbox. Until then `MemoryEventPublisher` keeps events
in memory (tests and single-process demos). Both check the data against the contract model for
the event type, so an invalid payload never gets out.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from careflow_contracts.events import DATA_MODEL_FOR_EVENT, EventType


@dataclass(frozen=True)
class Event:
    type: EventType
    data: BaseModel
    timestamp: datetime

    def envelope(self) -> dict[str, Any]:
        """The Standard Webhooks envelope (`packages/contracts/events/envelope.schema.json`)."""
        return {
            "type": self.type.value,
            "timestamp": self.timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "data": self.data.model_dump(mode="json", exclude_none=True),
        }


def checked(event: Event) -> Event:
    """Raise when the data is not the contract model of the event type."""
    DATA_MODEL_FOR_EVENT[event.type].model_validate(event.data.model_dump(mode="json"))
    return event


class EventPublisher(Protocol):
    async def add(self, db: AsyncSession, event: Event) -> None: ...


class MemoryEventPublisher:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def add(self, db: AsyncSession, event: Event) -> None:
        self.events.append(checked(event))


__all__ = ["Event", "EventPublisher", "MemoryEventPublisher", "checked"]
