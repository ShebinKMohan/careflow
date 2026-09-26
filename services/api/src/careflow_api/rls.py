"""Row-level security: every request transaction runs with `app.clinic_id` from the token.

Policies (alembic 0001) compare `clinic_id` with `NULLIF(current_setting('app.clinic_id', true),
'')::uuid`, so a transaction without the setting sees nothing (fail closed). `set_config(..., true)`
scopes the value to the transaction.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from careflow_api.db import get_sessionmaker

SET_CLINIC = text("SELECT set_config('app.clinic_id', :cid, true)")
SET_AUTH_EMAIL = text("SELECT set_config('app.auth_email', :email, true)")
SET_AUTH_DEVICE = text("SELECT set_config('app.auth_device_id', :device_id, true)")


@asynccontextmanager
async def clinic_session(clinic_id: uuid.UUID | str | None) -> AsyncIterator[AsyncSession]:
    """A session inside one transaction scoped to `clinic_id` (None: sees no clinic rows)."""
    async with get_sessionmaker()() as session, session.begin():
        if clinic_id is not None:
            await session.execute(SET_CLINIC, {"cid": str(clinic_id)})
        yield session
