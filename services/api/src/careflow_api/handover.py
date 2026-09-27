"""The QR hand-over (M2-06): a short-lived, signed, one-time token from the kiosk to the phone.

The kiosk shows `http://<companion>/claim#t=<qr_token>`: the token sits in the URL fragment, so no
browser sends it in a request or a log. It is a PyJWT HS256 token signed with its own key
(`CAREFLOW_HANDOVER_SIGNING_KEY`), never the API's JWT secret, with the claims `sid`,
`clinic_id`, `jti`, `aud = "careflow-companion"`, `iat` and `exp = iat + 120 s`. It carries no
personal data. The claim spends the `jti` once (`SET handover:jti:<jti> <proof> NX EX 300`, as the
voice agent's nonce store does), and marks the session handed over, after which every kiosk-channel
token of that session is refused.

A lost answer to a successful claim is recovered by the phone that claimed, not by a second claim:
the phone sends a random `claim_key` with the code and the claim keeps only its SHA-256 (the
proof). The same code with the same key, while the code is still valid, is that phone asking
again; any other replay is refused (ADR-048).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import uuid
from dataclasses import dataclass
from typing import Final

import jwt
from redis.asyncio import Redis

AUDIENCE: Final = "careflow-companion"
TTL: Final = dt.timedelta(seconds=120)
LEEWAY: Final = dt.timedelta(seconds=10)  # clock skew between the kiosk, the phone and the API
JTI_TTL_SECONDS: Final = 300
#: How long a handed-over session refuses kiosk tokens: longer than any patient token lives.
HANDED_OVER_TTL_SECONDS: Final = 2 * 60 * 60
ALGORITHM: Final = "HS256"
#: The proof a claim without a `claim_key` leaves: nothing can match it.
NO_CLAIM_KEY: Final = "-"


class InvalidHandover(Exception):
    """The QR token is expired, forged or not for the companion (401)."""


@dataclass(frozen=True, slots=True)
class HandoverClaims:
    session_id: str
    clinic_id: str
    jti: str


def issue(
    secret: str, *, session_id: str, clinic_id: str, now: dt.datetime
) -> tuple[str, dt.datetime]:
    """A QR token for one session and its expiry."""
    expires_at = now + TTL
    claims = {
        "sid": session_id,
        "clinic_id": clinic_id,
        "jti": uuid.uuid4().hex,
        "aud": AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return jwt.encode(claims, secret, algorithm=ALGORITHM), expires_at


def verify(secret: str, token: str) -> HandoverClaims:
    """The claims of a valid QR token; raises `InvalidHandover` otherwise."""
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            leeway=LEEWAY,
            options={"require": ["sid", "clinic_id", "jti", "aud", "iat", "exp"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidHandover(str(exc)) from exc
    return HandoverClaims(
        session_id=str(claims["sid"]), clinic_id=str(claims["clinic_id"]), jti=str(claims["jti"])
    )


def claim_proof(claim_key: str | None) -> str:
    """What a claim leaves behind: the SHA-256 of the phone's claim key; the key is never stored."""
    return hashlib.sha256(claim_key.encode()).hexdigest() if claim_key else NO_CLAIM_KEY


async def spend(redis: Redis, jti: str, proof: str = NO_CLAIM_KEY) -> bool:
    """True the first time a token is claimed (keeping the claimant's proof); False when it was
    already spent."""
    return bool(await redis.set(f"handover:jti:{jti}", proof, nx=True, ex=JTI_TTL_SECONDS))


async def same_claimant(redis: Redis, jti: str, claim_key: str | None) -> bool:
    """A spent token sent again by the phone that spent it: its key matches the kept proof. Never
    true without a key, or for a token claimed without one."""
    if not claim_key:
        return False
    kept = await redis.get(f"handover:jti:{jti}")
    if kept is None or kept == NO_CLAIM_KEY:
        return False
    return hmac.compare_digest(str(kept), claim_proof(claim_key))


async def mark_handed_over(redis: Redis, session_id: str, jti: str) -> bool:
    """Mark the session handed over by the token `jti`: True when this token did it (now, or on
    an earlier attempt of the same claim), False when another token handed it over."""
    key = f"handover:session:{session_id}"
    if await redis.set(key, jti, nx=True, ex=HANDED_OVER_TTL_SECONDS):
        return True
    return await redis.get(key) == jti


async def handed_over(redis: Redis, session_id: str) -> bool:
    return bool(await redis.exists(f"handover:session:{session_id}"))
