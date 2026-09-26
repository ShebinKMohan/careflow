"""`estimate_wait`: a rough, labelled estimate of a waiting token's wait (M1-10)."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Final

from careflow_api.queue.core import QueueCore, Token

BUCKET_MINUTES: Final = 5


def estimate_wait(token: Token, queue: QueueCore, service_minutes: Mapping[int, float]) -> int:
    """Minutes, an estimate only: the mean service minutes of every waiting token ahead of
    `token`, divided by the rooms that accept its level, rounded up to 5-minute buckets."""
    ahead = [t for t in queue.waiting() if t.order_key < token.order_key]
    rooms = sum(1 for room in queue.department.rooms if token.level in room.accepts_levels)
    minutes = sum(service_minutes[t.level] for t in ahead) / max(rooms, 1)
    return math.ceil(minutes / BUCKET_MINUTES) * BUCKET_MINUTES


__all__ = ["BUCKET_MINUTES", "estimate_wait"]
