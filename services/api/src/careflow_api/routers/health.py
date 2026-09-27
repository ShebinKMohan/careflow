"""GET /health: liveness plus the reachability of Postgres, Valkey and HAPI FHIR (no auth)."""

from __future__ import annotations

import asyncio
from typing import Annotated, Literal

import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends
from sqlalchemy import text

from careflow_api.config import Settings, get_settings
from careflow_api.db import get_engine
from careflow_api.hospital import fhir_endpoint
from careflow_contracts.api_models import Health

router = APIRouter(tags=["health"])
Component = Literal["ok", "down"]


async def check_db() -> Component:
    try:
        async with asyncio.timeout(2):
            async with get_engine().connect() as connection:
                await connection.execute(text("SELECT 1"))
        return "ok"
    except Exception:  # noqa: BLE001 - any failure means down
        return "down"


async def check_redis(url: str) -> Component:
    client = aioredis.Redis.from_url(url, socket_timeout=1, socket_connect_timeout=1)
    try:
        return "ok" if await client.ping() else "down"
    except Exception:  # noqa: BLE001
        return "down"
    finally:
        await client.aclose()


async def check_fhir(base_url: str) -> Component:
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            response = await client.get(
                f"{base_url.rstrip('/')}/metadata", params={"_summary": "true"}
            )
        return "ok" if response.status_code == 200 else "down"
    except httpx.HTTPError:
        return "down"


async def component_status(settings: Settings) -> dict[str, Component]:
    db, redis_status, fhir = await asyncio.gather(
        check_db(), check_redis(settings.redis_url), check_fhir(fhir_endpoint())
    )
    return {"db": db, "redis": redis_status, "fhir": fhir}


@router.get("/health", response_model=Health)
async def health(settings: Annotated[Settings, Depends(get_settings)]) -> Health:
    return Health.model_validate(
        {"status": "ok", "version": settings.api_version, **await component_status(settings)}
    )
