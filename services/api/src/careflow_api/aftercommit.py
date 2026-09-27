"""Run async work once a request's transaction has committed (M2-07).

The request's database session (`deps.get_db`) commits when its dependency exits, which is after
the route and even after FastAPI's background tasks. Work that must see the committed state (the
queue's `queue.status` broadcast) is scheduled from the session's `after_commit` event instead: it
never runs on a rollback.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)
_running: set[asyncio.Task[None]] = set()  # strong references until each task is done


def after_commit(db: AsyncSession, run: Callable[[], Awaitable[None]]) -> None:
    """Start `run()` once `db`'s transaction commits."""
    loop = asyncio.get_running_loop()

    async def guarded() -> None:
        try:
            await run()
        except Exception:
            logger.exception("after_commit.failed")

    def schedule(_session: Any) -> None:
        task = loop.create_task(guarded())
        _running.add(task)
        task.add_done_callback(_running.discard)

    event.listen(db.sync_session, "after_commit", schedule, once=True)


async def drain() -> None:
    """Wait for the work already started on this event loop (a test's teardown)."""
    loop = asyncio.get_running_loop()
    await asyncio.gather(*[t for t in _running if t.get_loop() is loop], return_exceptions=True)


__all__ = ["after_commit", "drain"]
