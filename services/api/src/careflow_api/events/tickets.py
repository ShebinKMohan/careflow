"""One-time WebSocket tickets (M1-11). `POST /v1/ws-ticket` stores a single-use ticket in Redis
for 30 s, so a JWT never goes in a URL or a log; the socket redeems it atomically (`GETDEL`)."""

from __future__ import annotations

import json
import secrets
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Final, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from redis.asyncio import Redis

from careflow_api.deps import Principal, require_roles
from careflow_contracts.api_models import WsTicket, WsTicketRequest

TTL_SECONDS: Final = 30
PREFIX: Final = "careflow:ws-ticket:"
STAFF_ROLES: Final = frozenset({"nurse", "doctor", "board", "admin"})
SESSION_ROLES: Final = frozenset({"kiosk_device", "patient_session"})

router = APIRouter(tags=["realtime"])


@dataclass(frozen=True)
class TicketClaims:
    role: str
    clinic_id: str
    subject: str
    channel: Literal["staff", "session"]
    session_id: str | None = None


async def issue(redis: Redis, claims: TicketClaims, *, now: datetime) -> tuple[str, datetime]:
    ticket = secrets.token_urlsafe(32)
    await redis.set(PREFIX + ticket, json.dumps(asdict(claims)), ex=TTL_SECONDS, nx=True)
    return ticket, now + timedelta(seconds=TTL_SECONDS)


async def redeem(redis: Redis, ticket: str | None) -> TicketClaims | None:
    """The ticket's claims, once: a used, expired or unknown ticket gives None."""
    if not ticket:
        return None
    raw = await redis.getdel(PREFIX + ticket)
    return None if raw is None else TicketClaims(**json.loads(raw))


def redis_of(request: Request) -> Redis:
    redis: Redis = request.app.state.redis
    return redis


@router.post("/v1/ws-ticket", response_model=WsTicket)
async def create_ticket(
    body: WsTicketRequest,
    principal: Annotated[
        Principal,
        Depends(
            require_roles("kiosk_device", "patient_session", "nurse", "doctor", "board", "admin")
        ),
    ],
    redis: Annotated[Redis, Depends(redis_of)],
) -> WsTicket:
    channel = body.channel.value
    if channel == "staff" and principal.role not in STAFF_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This role has no staff channel.")
    if channel == "session":
        if body.session_id is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="The session channel needs session_id.",
            )
        if principal.role not in SESSION_ROLES or (
            principal.role == "patient_session" and principal.session_id != body.session_id
        ):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not this session's channel.")
    claims = TicketClaims(
        role=principal.role,
        clinic_id=str(principal.clinic_id),
        subject=principal.subject,
        channel="staff" if channel == "staff" else "session",
        session_id=body.session_id if channel == "session" else None,
    )
    ticket, expires_at = await issue(redis, claims, now=datetime.now(UTC))
    return WsTicket(
        ticket=ticket, channel=body.channel, session_id=claims.session_id, expires_at=expires_at
    )


__all__ = [
    "PREFIX",
    "SESSION_ROLES",
    "STAFF_ROLES",
    "TTL_SECONDS",
    "TicketClaims",
    "issue",
    "redeem",
    "router",
]
