"""The transactional outbox (M1-11): `OutboxPublisher.add(db, event)` writes the event in the
caller's transaction, so a rolled-back change never publishes and a committed one always does.

The row is validated first (`publisher.checked`), then inserted with
`ON CONFLICT (clinic_id, idempotency_key) DO NOTHING`: the same key twice is one event. RLS scopes
the insert to the caller's clinic, so an event can only be written for that clinic.
"""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from careflow_api.events.publisher import Event, checked
from careflow_api.models import OutboxEvent

#: The contract version the payloads follow (`packages/contracts/openapi.yaml` info.version).
SCHEMA_VERSION: Final = "0.1.0"


class OutboxPublisher:
    async def add(self, db: AsyncSession, event: Event) -> None:
        checked(event)
        statement = insert(OutboxEvent).values(
            event_type=str(event.type),
            schema_version=SCHEMA_VERSION,
            clinic_id=uuid.UUID(event.clinic_id),
            session_id=event.session_id,
            idempotency_key=event.idempotency_key or str(uuid.uuid4()),
            payload=event.envelope(),
        )
        await db.execute(
            statement.on_conflict_do_nothing(
                index_elements=[OutboxEvent.clinic_id, OutboxEvent.idempotency_key]
            )
        )


__all__ = ["SCHEMA_VERSION", "OutboxPublisher"]
