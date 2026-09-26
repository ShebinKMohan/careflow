"""Stable fact ids: the same statement or resource always gets the same contract fact id."""

from __future__ import annotations

import hashlib
from typing import Final

CROCKFORD: Final = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def stable_fact_id(*parts: str) -> str:
    """A contract fact id (`f_` + 26 Crockford base32 characters) derived from `parts`."""
    number = int.from_bytes(hashlib.sha256("|".join(parts).encode()).digest()[:17], "big")
    chars: list[str] = []
    for _ in range(26):
        chars.append(CROCKFORD[number & 31])
        number >>= 5
    return "f_" + "".join(reversed(chars))


__all__ = ["CROCKFORD", "stable_fact_id"]
