"""Typed access to Redis Stream replies (M1-11). The client decodes responses to `str`; these
helpers give the rest of the code plain `(id, fields)` pairs."""

from __future__ import annotations

from typing import Any

from redis.asyncio import Redis
from redis.typing import EncodableT, FieldT

type Entry = tuple[str, dict[str, str]]


def entries(raw: Any) -> list[Entry]:
    """`(id, fields)` pairs of an XRANGE or XAUTOCLAIM reply; entries whose data was trimmed
    from the stream (no fields) are left out."""
    found: list[Entry] = []
    for stream_id, fields in raw or []:
        if fields is not None:
            found.append((str(stream_id), {str(k): str(v) for k, v in fields.items()}))
    return found


def read_entries(raw: Any) -> list[Entry]:
    """The entries of an XREAD or XREADGROUP reply (`[[stream, entries], ...]`)."""
    return [entry for _, stream_entries in raw or [] for entry in entries(stream_entries)]


async def xadd(redis: Redis, stream: str, fields: dict[str, str], *, maxlen: int) -> str:
    payload: dict[FieldT, EncodableT] = dict(fields.items())
    return str(await redis.xadd(stream, payload, maxlen=maxlen, approximate=True))


async def xrange(
    redis: Redis, stream: str, *, start: str = "-", count: int | None = None
) -> list[Entry]:
    return entries(await redis.xrange(stream, min=start, max="+", count=count))


__all__ = ["Entry", "entries", "read_entries", "xadd", "xrange"]
