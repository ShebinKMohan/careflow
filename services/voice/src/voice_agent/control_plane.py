"""Best-effort agent -> control-plane call ingestion.

Wraps a CallSessionStore so every lifecycle upsert is mirrored to the control
plane's /ingest/calls endpoint (project API-key auth). This makes dashboard calls
advance past 'requested' to real terminal states. Fire-and-forget: a control-plane
outage never blocks or fails the realtime call path.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

import httpx

from voice_agent.per_call_config import PerCallLLMConfig
from voice_agent.storage.call_events import CallEventStore
from voice_agent.storage.call_sessions import CallSessionStore, CallSessionUpdate

logger = logging.getLogger(__name__)


class ControlPlaneEventClient(Protocol):
    def fire_event(self, event: dict[str, Any]) -> None: ...


class ControlPlaneClient:
    def __init__(self, base_url: str, api_key: str, *, timeout_seconds: float = 5.0) -> None:
        base = base_url.rstrip("/")
        self._url = base + "/ingest/calls"
        self._inbound_config_url = base + "/ingest/inbound-config"
        self._headers = {"X-API-Key": api_key, "content-type": "application/json"}
        self._client = httpx.AsyncClient(timeout=timeout_seconds)
        self._tasks: set[asyncio.Task[None]] = set()

    async def fetch_inbound_config(
        self, number: str, *, timeout_seconds: float = 2.5
    ) -> PerCallLLMConfig | None:
        """Look up a dialed number's per-call LLM overrides for an inbound call.
        Best-effort: returns None on any failure so the call proceeds on env defaults."""
        try:
            response = await self._client.get(
                self._inbound_config_url,
                params={"number": number},
                headers=self._headers,
                timeout=timeout_seconds,
            )
        except Exception:
            logger.debug("control-plane inbound-config lookup failed.", exc_info=True)
            return None
        if response.status_code != 200:
            return None
        try:
            data = response.json()
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        provider = data.get("provider")
        model = data.get("model")
        prompt = data.get("system_prompt")
        greeting = data.get("first_message")
        temperature = data.get("temperature")
        behavior = data.get("behavior")
        stt = data.get("stt")
        tts = data.get("tts")
        config = PerCallLLMConfig(
            provider=(
                provider.strip().lower() if isinstance(provider, str) and provider.strip() else None
            ),
            model=model.strip() if isinstance(model, str) and model.strip() else None,
            system_prompt=prompt.strip() if isinstance(prompt, str) and prompt.strip() else None,
            greeting=greeting.strip() if isinstance(greeting, str) and greeting.strip() else None,
            temperature=(
                float(temperature)
                if isinstance(temperature, int | float) and not isinstance(temperature, bool)
                else None
            ),
            behavior=behavior if isinstance(behavior, dict) else None,
            stt=stt if isinstance(stt, dict) else None,
            tts=tts if isinstance(tts, dict) else None,
        )
        return None if config.is_empty else config

    def fire(self, update: CallSessionUpdate) -> None:
        payload = _to_ingest_payload(update)
        if payload is None:
            return
        task = asyncio.create_task(self._post(payload))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def fire_event(self, event: dict[str, Any]) -> None:
        payload = _event_to_ingest_payload(event)
        if payload is None:
            return
        task = asyncio.create_task(self._post(payload))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _post(self, payload: dict[str, Any]) -> None:
        try:
            await self._client.post(self._url, json=payload, headers=self._headers)
        except Exception:
            logger.debug("control-plane ingest failed (best-effort).", exc_info=True)

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await self._client.aclose()


def _to_ingest_payload(update: CallSessionUpdate) -> dict[str, Any] | None:
    if not (update.status or update.transcript):
        return None
    if not (update.call_id or update.request_uuid or update.idempotency_key):
        return None
    payload: dict[str, Any] = {}
    if update.status:
        payload["status"] = update.status
    if update.call_id:
        payload["provider_call_id"] = update.call_id
    if update.request_uuid:
        payload["request_uuid"] = update.request_uuid
    if update.idempotency_key:
        payload["idempotency_key"] = update.idempotency_key
    if update.direction:
        payload["direction"] = update.direction
    if update.from_number:
        payload["from_number"] = update.from_number
    if update.to_number:
        payload["to_number"] = update.to_number
    if update.event:
        payload["event"] = update.event
    elif update.transcript:
        payload["event"] = "transcript_updated"
    if update.transcript:
        payload["transcript"] = update.transcript
    if update.last_event:
        payload["last_event"] = update.last_event
    return payload


def _event_to_ingest_payload(event: dict[str, Any]) -> dict[str, Any] | None:
    event_type = _clean_text(event.get("event")) or _clean_text(event.get("Event"))
    provider_call_id = (
        _clean_text(event.get("call_id"))
        or _clean_text(event.get("provider_call_id"))
        or _clean_text(event.get("CallUUID"))
    )
    request_uuid = _clean_text(event.get("request_uuid")) or _clean_text(event.get("RequestUUID"))
    idempotency_key = _clean_text(event.get("idempotency_key"))
    if not event_type:
        return None
    if not (provider_call_id or request_uuid or idempotency_key):
        return None

    payload: dict[str, Any] = {"event": event_type, "last_event": event}
    if provider_call_id:
        payload["provider_call_id"] = provider_call_id
    if request_uuid:
        payload["request_uuid"] = request_uuid
    if idempotency_key:
        payload["idempotency_key"] = idempotency_key
    return payload


def _clean_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


class IngestingCallSessionStore:
    """CallSessionStore decorator that mirrors lifecycle upserts to the control plane."""

    def __init__(self, inner: CallSessionStore, client: ControlPlaneClient) -> None:
        self._inner = inner
        self._client = client

    async def upsert(self, update: CallSessionUpdate) -> dict[str, Any]:
        result = await self._inner.upsert(update)
        self._client.fire(update)
        return result

    async def reserve_idempotency_key(self, update: CallSessionUpdate) -> dict[str, Any] | None:
        result = await self._inner.reserve_idempotency_key(update)
        self._client.fire(update)
        return result

    async def get(self, identifier: str) -> dict[str, Any] | None:
        return await self._inner.get(identifier)

    async def health_check(self) -> bool:
        return await self._inner.health_check()

    async def close(self) -> None:
        await self._client.close()
        await self._inner.close()


class IngestingCallEventStore:
    """CallEventStore decorator that mirrors audit/latency events to the control plane."""

    def __init__(self, inner: CallEventStore, client: ControlPlaneEventClient) -> None:
        self._inner = inner
        self._client = client

    async def record(self, event: dict[str, Any]) -> None:
        await self._inner.record(event)
        self._client.fire_event(event)

    async def recent(self, *, count: int) -> list[dict[str, Any]]:
        return await self._inner.recent(count=count)

    async def health_check(self) -> bool:
        return await self._inner.health_check()

    async def close(self) -> None:
        await self._inner.close()
