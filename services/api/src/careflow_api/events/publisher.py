"""`EventPublisher.add(db, event)`: publish an event inside the caller's DB transaction.

`outbox.OutboxPublisher` (M1-11) writes it to the transactional outbox, so it exists exactly when
the change commits. `MemoryEventPublisher` keeps events in memory for unit tests. Both check the
data against the contract model of the event type first, so an invalid payload never gets out.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from careflow_contracts.events import DATA_MODEL_FOR_EVENT, INTERNAL_DATA_MODELS

#: Every event type CareFlow publishes: the webhook types, then the internal (bus-only) ones.
DATA_MODELS: Final[dict[str, type[BaseModel]]] = {
    **{event_type.value: model for event_type, model in DATA_MODEL_FOR_EVENT.items()},
    **INTERNAL_DATA_MODELS,
}


@dataclass(frozen=True)
class Event:
    #: An `EventType` value or an internal type (`careflow_contracts.events`).
    type: str
    data: BaseModel
    timestamp: datetime
    #: Same key, same event: a second `add` with it is ignored. None: a fresh key.
    idempotency_key: str | None = None

    @property
    def clinic_id(self) -> str:
        return str(self.data.model_dump(mode="json")["clinic_id"])

    @property
    def session_id(self) -> str | None:
        value = self.data.model_dump(mode="json").get("session_id")
        return None if value is None else str(value)

    def envelope(self) -> dict[str, Any]:
        """The Standard Webhooks envelope (`packages/contracts/events/envelope.schema.json`)."""
        return {
            "type": str(self.type),
            "timestamp": self.timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "data": self.data.model_dump(mode="json", exclude_none=True),
        }


def checked(event: Event) -> Event:
    """Raise unless the data is the contract model of a known event type."""
    model = DATA_MODELS.get(str(event.type))
    if model is None:
        raise ValueError(f"unknown event type {event.type!r}")
    model.model_validate(event.data.model_dump(mode="json"))
    return event


class EventPublisher(Protocol):
    async def add(self, db: AsyncSession, event: Event) -> None: ...


class MemoryEventPublisher:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def add(self, db: AsyncSession, event: Event) -> None:
        self.events.append(checked(event))


__all__ = ["DATA_MODELS", "Event", "EventPublisher", "MemoryEventPublisher", "checked"]
