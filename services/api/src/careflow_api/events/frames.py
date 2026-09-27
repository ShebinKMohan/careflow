"""WebSocket-only frames (M2-06): published straight to the event stream, never through the
outbox, so they never become webhooks. The live hub hands them to the connections that want the
type (`events/ws.py` SESSION_TYPES); the frame is `{id, type, timestamp, data}` like any other."""

from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any

from redis.asyncio import Redis

from careflow_api.events.relay import MAXLEN, STREAM
from careflow_api.events.streams import xadd

SESSION_HANDED_OVER = "session.handed_over"


async def publish_frame(
    redis: Redis,
    *,
    frame_type: str,
    clinic_id: str,
    session_id: str,
    data: dict[str, Any],
    now: dt.datetime,
    stream: str = STREAM,
) -> str:
    """Add one frame for one session's subscribers; returns the stream id."""
    timestamp = now.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
    envelope = {"type": frame_type, "timestamp": timestamp, "data": data}
    fields = {
        "id": str(uuid.uuid4()),
        "type": frame_type,
        "clinic_id": clinic_id,
        "session_id": session_id,
        "payload": json.dumps(envelope, separators=(",", ":"), ensure_ascii=False),
        "created_at": now.isoformat(),
    }
    return await xadd(redis, stream, fields, maxlen=MAXLEN)


__all__ = ["SESSION_HANDED_OVER", "publish_frame"]
