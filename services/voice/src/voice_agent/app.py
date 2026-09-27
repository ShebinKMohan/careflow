from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import re
import socket
import time
import uuid
import wave
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from secrets import compare_digest, token_urlsafe
from typing import Any, Protocol, cast
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, ValidationError, field_validator

from voice_agent.config.settings import Settings, get_settings
from voice_agent.latency import summarize_voice_turn_latency_events
from voice_agent.media.session import (
    END_CALL_MARKER,
    VoiceActionRequest,
    VoiceTurnLatencySnapshot,
)
from voice_agent.metrics import CONTENT_TYPE as METRICS_CONTENT_TYPE
from voice_agent.metrics import MetricsRegistry
from voice_agent.observability import add_observability_headers
from voice_agent.per_call_config import (
    PerCallConfigStore,
    PerCallLLMConfig,
    build_per_call_config_store,
)
from voice_agent.providers.factory import (
    build_media_session_runner,
    generate_preview_reply,
    generate_tts_preview_audio,
    prewarm_cached_tts_audio,
)
from voice_agent.ratelimit import SlidingWindowRateLimiter
from voice_agent.security.nonce_store import NonceStore, build_nonce_store
from voice_agent.security.plivo_signature import (
    validate_v3_signature,
    validate_websocket_v3_signature,
)
from voice_agent.security.stream_token import (
    StreamTokenClaims,
    build_stream_token,
    verify_stream_token,
)
from voice_agent.storage.call_events import CallEventStore, build_call_event_store
from voice_agent.storage.call_sessions import (
    CallSessionStore,
    CallSessionUpdate,
    build_call_session_store,
    is_terminal_call_status,
)
from voice_agent.telephony.plivo import (
    PlivoAudioFormat,
    PlivoProtocolError,
    build_answer_xml,
    build_hangup_xml,
    extract_start_identifiers,
    parse_plivo_message,
)
from voice_agent.telephony.plivo_client import (
    OutboundCallClient,
    PlivoCallClientError,
    PlivoOutboundCallClient,
)

logger = logging.getLogger(__name__)
E164_PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")
IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
CONTROL_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
CALLBACK_FIELD_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
CALLBACK_LIFECYCLE_VALUE_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
CALLBACK_ENDPOINT_VALUE_RE = re.compile(r"^[A-Za-z0-9+@._:;=()-]{1,128}$")
CALLBACK_SEMANTIC_ALIAS_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("direction", ("Direction", "direction")),
    ("from number", ("From", "from", "FromNumber", "from_number")),
    ("to number", ("To", "to", "ToNumber", "to_number")),
    ("status", ("CallStatus", "call_status")),
    ("event", ("Event", "event")),
)
OUTBOUND_CALLBACK_HEALTH_CHECK_TIMEOUT_SECONDS = 2.0
CALLBACK_LIFECYCLE_VALUE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("status", ("CallStatus", "call_status")),
    ("event", ("Event", "event")),
)
CALLBACK_DIRECTION_VALUE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("direction", ("Direction", "direction")),
)
CALLBACK_ENDPOINT_VALUE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("from number", ("From", "from", "FromNumber", "from_number")),
    ("to number", ("To", "to", "ToNumber", "to_number")),
)


class DuplicateCallbackFieldError(ValueError):
    """Raised when a callback payload repeats the same field name."""


class InvalidCallbackFieldNameError(ValueError):
    """Raised when a callback payload includes an unsafe field name."""


class UnsafeCallbackFieldNameError(ValueError):
    """Raised when a callback payload field name contains unsafe characters."""


class ActiveMediaRunner(Protocol):
    @property
    def call_id(self) -> str: ...

    @property
    def stream_id(self) -> str: ...

    async def close(self) -> None: ...


class OutboundCallRequest(BaseModel):
    to: str = Field(..., min_length=8, max_length=16)
    # Optional per-call LLM overrides forwarded by the control-plane from a
    # project's active agent-config. Omitted -> the agent's env defaults apply.
    provider: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, max_length=128)
    system_prompt: str | None = Field(default=None, max_length=8000)
    first_message: str | None = Field(default=None, max_length=2000)
    temperature: float | None = Field(default=None, ge=0, le=2)
    behavior: dict[str, Any] | None = None
    stt: dict[str, Any] | None = None
    tts: dict[str, Any] | None = None
    from_number: str | None = Field(default=None, max_length=16)

    @field_validator("to", mode="before")
    @classmethod
    def trim_destination(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator(
        "provider", "model", "system_prompt", "first_message", "from_number", mode="before"
    )
    @classmethod
    def blank_override_to_none(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("from_number")
    @classmethod
    def validate_from_number(cls, value: str | None) -> str | None:
        if value is not None and not E164_PHONE_RE.fullmatch(value):
            raise ValueError("from_number must be a single E.164 phone number.")
        return value

    @field_validator("to")
    @classmethod
    def validate_e164_destination(cls, value: str) -> str:
        if not E164_PHONE_RE.fullmatch(value):
            raise ValueError("to must be a single E.164 phone number, for example +14155551234.")
        return value


class PreviewTurnRequest(BaseModel):
    user_text: str = Field(..., min_length=1, max_length=4096)
    provider: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, max_length=128)
    system_prompt: str | None = Field(default=None, max_length=8000)
    temperature: float | None = Field(default=None, ge=0, le=2)
    behavior: dict[str, Any] | None = None

    @field_validator("user_text", "provider", "model", "system_prompt", mode="before")
    @classmethod
    def trim_preview_strings(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("provider", "model", "system_prompt", mode="before")
    @classmethod
    def blank_preview_override_to_none(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value


class PreviewTTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=300)
    provider: str | None = Field(default=None, max_length=40)
    tts: dict[str, Any] | None = None

    @field_validator("text", "provider", mode="before")
    @classmethod
    def trim_tts_preview_strings(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("provider", mode="before")
    @classmethod
    def blank_tts_preview_provider_to_none(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value


def create_app(
    settings: Settings | None = None,
    *,
    call_event_store: CallEventStore | None = None,
    call_session_store: CallSessionStore | None = None,
    outbound_call_client: OutboundCallClient | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    app = FastAPI(
        title="Plivo Voice Agent Backend",
        version="0.1.0",
        openapi_url="/openapi.json" if resolved_settings.environment != "production" else None,
        docs_url="/docs" if resolved_settings.environment != "production" else None,
        redoc_url=None,
        lifespan=_lifespan,
    )
    app.state.settings = resolved_settings
    resolved_call_event_store = call_event_store or build_call_event_store(resolved_settings)
    resolved_call_session_store = call_session_store or build_call_session_store(resolved_settings)
    control_plane_client = None
    if resolved_settings.control_plane_base_url and resolved_settings.control_plane_ingest_api_key:
        from voice_agent.control_plane import (
            ControlPlaneClient,
            IngestingCallEventStore,
            IngestingCallSessionStore,
        )

        control_plane_client = ControlPlaneClient(
            resolved_settings.control_plane_base_url,
            resolved_settings.control_plane_ingest_api_key.get_secret_value(),
        )
        resolved_call_event_store = IngestingCallEventStore(
            resolved_call_event_store, control_plane_client
        )
        resolved_call_session_store = IngestingCallSessionStore(
            resolved_call_session_store, control_plane_client
        )
    app.state.call_event_store = resolved_call_event_store
    app.state.call_session_store = resolved_call_session_store
    # Used by /plivo/answer to resolve a dialed number's per-call config for inbound calls.
    app.state.control_plane_client = control_plane_client
    app.state.outbound_call_client = outbound_call_client or _build_outbound_call_client(
        resolved_settings
    )
    # Bridges a project's per-call overrides from /calls/outbound (or inbound answer)
    # to the media WS; Redis-backed when session_backend=redis so it spans workers.
    app.state.per_call_config_store = build_per_call_config_store(resolved_settings)
    # Graceful-shutdown coordination: `shutting_down` flips /readyz to not_ready so the load
    # balancer stops routing new calls, and `active_media_runners` lets the lifespan drain
    # in-flight calls before tearing down the storage clients. `media_drained` is set whenever
    # no call is active, so shutdown can wait for the last call to finish without polling.
    app.state.shutting_down = False
    app.state.active_media_runners = set()
    app.state.media_drained = asyncio.Event()
    app.state.media_drained.set()
    app.state.metrics = MetricsRegistry()
    # Single-use nonce tracking for opt-in replay protection (stream tokens + webhooks).
    app.state.nonce_store = build_nonce_store(
        session_backend=resolved_settings.session_backend,
        redis_url=resolved_settings.redis_url,
        socket_timeout_seconds=resolved_settings.redis_socket_timeout_seconds,
        connect_timeout_seconds=resolved_settings.redis_connect_timeout_seconds,
        health_check_interval_seconds=resolved_settings.redis_health_check_interval_seconds,
        max_connections=resolved_settings.redis_max_connections,
    )
    app.state.control_rate_limiter = (
        SlidingWindowRateLimiter(
            max_requests=resolved_settings.control_rate_limit_per_minute,
            window_seconds=60.0,
        )
        if resolved_settings.rate_limit_enabled
        else None
    )
    app.middleware("http")(add_observability_headers)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        # "service" tells CareFlow's voice service apart from another copy of the voice agent
        # on the same port (the API's admin status probes it, M1-17).
        return {"status": "ok", "service": "careflow-voice"}

    @app.get("/metrics")
    async def metrics() -> Response:
        # Unauthenticated so a Prometheus scraper can read it (unlike /latency/summary).
        registry = cast(MetricsRegistry, app.state.metrics)
        active_sessions = len(cast("set[ActiveMediaRunner]", app.state.active_media_runners))
        return Response(
            content=registry.render(active_media_sessions=active_sessions),
            media_type=METRICS_CONTENT_TYPE,
        )

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        event_store_is_healthy = await _run_readiness_check(
            "call_event_store",
            app.state.call_event_store.health_check(),
            timeout_seconds=resolved_settings.readiness_check_timeout_seconds,
        )
        session_store_is_healthy = await _run_readiness_check(
            "call_session_store",
            app.state.call_session_store.health_check(),
            timeout_seconds=resolved_settings.readiness_check_timeout_seconds,
        )
        per_call_config_store_is_healthy = await _run_readiness_check(
            "per_call_config_store",
            app.state.per_call_config_store.health_check(),
            timeout_seconds=resolved_settings.readiness_check_timeout_seconds,
        )
        luxtts_sidecar_is_healthy = True
        provider_details: dict[str, object] = {}
        if resolved_settings.tts_provider == "luxtts":
            luxtts_sidecar_is_healthy, luxtts_sidecar_detail = await _luxtts_sidecar_readiness(
                resolved_settings
            )
            provider_details["luxtts_sidecar"] = luxtts_sidecar_detail
        missing_local_speech_models = _missing_local_speech_model_files(resolved_settings)
        local_speech_is_healthy = not missing_local_speech_models
        is_shutting_down = bool(getattr(app.state, "shutting_down", False))
        is_ready = (
            event_store_is_healthy
            and session_store_is_healthy
            and per_call_config_store_is_healthy
            and luxtts_sidecar_is_healthy
            and local_speech_is_healthy
            and not is_shutting_down
        )
        checks = {
            "call_event_store": "ok" if event_store_is_healthy else "unhealthy",
            "call_session_store": "ok" if session_store_is_healthy else "unhealthy",
            "per_call_config_store": ("ok" if per_call_config_store_is_healthy else "unhealthy"),
            "accepting_calls": "no" if is_shutting_down else "yes",
        }
        if resolved_settings.tts_provider == "luxtts":
            checks["luxtts_sidecar"] = "ok" if luxtts_sidecar_is_healthy else "unhealthy"
        if missing_local_speech_models is not None:
            checks["local_speech_models"] = "ok" if local_speech_is_healthy else "unhealthy"
            if missing_local_speech_models:
                provider_details["local_speech_models"] = {"missing": missing_local_speech_models}
        response_body: dict[str, object] = {
            "status": "ready" if is_ready else "not_ready",
            "providers": _provider_status(resolved_settings),
            "public_base_url": resolved_settings.public_base_url,
            "checks": checks,
        }
        if provider_details:
            response_body["provider_details"] = provider_details
        return JSONResponse(
            status_code=200 if is_ready else 503,
            content=response_body,
        )

    @app.post("/plivo/answer")
    async def plivo_answer(request: Request) -> Response:
        params = await _read_callback_payload(
            request,
            max_body_bytes=resolved_settings.callback_max_body_bytes,
        )
        _validate_plivo_request(request, resolved_settings, params)
        await _enforce_webhook_replay_protection(app, resolved_settings, request)
        _validate_callback_identifiers(params)
        _validate_callback_semantic_aliases(params)
        _validate_callback_lifecycle_values(params)
        _validate_callback_session_metadata_values(params)
        try:
            await _record_callback_event(app.state.call_event_store, params)
        except Exception as exc:
            logger.exception("Call event storage failed while recording Plivo answer callback.")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Call event storage is unavailable.",
            ) from exc
        try:
            await _record_answer_session(app.state.call_session_store, params)
        except Exception as exc:
            logger.exception("Call session storage failed while recording Plivo answer callback.")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Call session storage is unavailable.",
            ) from exc
        cfg_id = request.query_params.get("cfg")
        disabled_inbound_message: str | None = None
        if cfg_id is None:
            cfg_id, disabled_inbound_message = await _resolve_inbound_cfg(app, params)
        if disabled_inbound_message is not None:
            return Response(
                content=build_hangup_xml(disabled_inbound_message),
                media_type="application/xml",
            )
        xml = build_answer_xml(
            websocket_url=_build_answer_media_websocket_url(resolved_settings, params, cfg_id),
            status_callback_url=resolved_settings.status_callback_url,
            status_callback_method=resolved_settings.stream_status_callback_method,
            stream_timeout_seconds=resolved_settings.stream_timeout_seconds,
            audio_format=PlivoAudioFormat.L16_16KHZ,
            noise_cancellation=resolved_settings.stream_noise_cancellation,
            noise_cancellation_level=resolved_settings.stream_noise_cancellation_level,
        )
        return Response(content=xml, media_type="application/xml")

    @app.api_route("/plivo/status", methods=["GET", "POST"])
    async def plivo_status(request: Request) -> dict[str, str]:
        _validate_stream_status_callback_method(request, resolved_settings)
        params = await _read_callback_payload(
            request,
            max_body_bytes=resolved_settings.callback_max_body_bytes,
        )
        if not _validate_plivo_request(request, resolved_settings, params, fatal=False):
            # Plivo's media-STREAM status callbacks (identified by StreamID) sign in a form
            # that can't be reliably reconstructed; a 403 there makes Plivo abort the live
            # call, so acknowledge those without recording (the call's lifecycle is tracked
            # from the media WebSocket session). Ordinary call-status callbacks stay strict.
            if "StreamID" in params or "stream_id" in params:
                if resolved_settings.enforce_stream_status_signature:
                    raise HTTPException(
                        status_code=403, detail="Invalid Plivo stream-status signature."
                    )
                logger.warning(
                    "Unverified Plivo stream-status callback; acknowledged without recording."
                )
                return {"status": "accepted"}
            raise HTTPException(status_code=403, detail="Invalid Plivo signature.")
        await _enforce_webhook_replay_protection(app, resolved_settings, request)
        _validate_callback_identifiers(params)
        _validate_callback_semantic_aliases(params)
        _validate_callback_lifecycle_values(params)
        _validate_callback_session_metadata_values(params)
        try:
            await _record_callback_event(app.state.call_event_store, params)
        except Exception as exc:
            logger.exception("Call event storage failed while recording Plivo status callback.")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Call event storage is unavailable.",
            ) from exc
        try:
            await _record_status_session(app.state.call_session_store, params)
        except Exception as exc:
            logger.exception("Call session storage failed while recording Plivo status callback.")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Call session storage is unavailable.",
            ) from exc
        return {"status": "accepted"}

    @app.post("/calls/outbound", status_code=status.HTTP_202_ACCEPTED)
    async def create_outbound_call(request: Request) -> JSONResponse:
        _validate_control_request(request, resolved_settings)
        _enforce_control_rate_limit(request, app)
        idempotency_key = _read_idempotency_key(request)
        payload = await _read_outbound_call_request(
            request,
            max_body_bytes=resolved_settings.control_max_body_bytes,
        )
        call_client = app.state.outbound_call_client
        # Per-project caller ID: use the number the control-plane forwarded, falling back to
        # the agent's configured default when none was supplied.
        from_number = payload.from_number or resolved_settings.plivo_from_number
        # Dry-run (dev/test only) simulates the dial so the whole chain is testable without
        # placing a real call; production always dials for real.
        dry_run = (
            resolved_settings.outbound_dry_run and resolved_settings.environment != "production"
        )
        if from_number is None or (call_client is None and not dry_run):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Outbound calling is not configured.",
            )
        session_store = cast(CallSessionStore, app.state.call_session_store)
        try:
            existing_session = await session_store.reserve_idempotency_key(
                CallSessionUpdate(
                    idempotency_key=idempotency_key,
                    direction="outbound",
                    from_number=from_number,
                    to_number=payload.to,
                    status="creating",
                    event="outbound_create_requested",
                )
            )
        except Exception as exc:
            logger.exception("Call session storage failed while reserving outbound call.")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Call session storage is unavailable.",
            ) from exc
        if existing_session is not None:
            return _replay_outbound_idempotency_result(
                existing_session=existing_session,
                from_number=from_number,
                to_number=payload.to,
            )
        answer_url = resolved_settings.answer_callback_url
        per_call_config = PerCallLLMConfig(
            provider=payload.provider,
            model=payload.model,
            system_prompt=payload.system_prompt,
            greeting=payload.first_message,
            temperature=payload.temperature,
            behavior=payload.behavior,
            stt=payload.stt,
            tts=payload.tts,
        )
        if not per_call_config.is_empty:
            cfg_id = token_urlsafe(18)
            try:
                await app.state.per_call_config_store.put(cfg_id, per_call_config)
            except Exception as exc:
                # The idempotency row is already reserved as "creating". If we cannot stash
                # the per-call config we must finalize that reservation as failed instead of
                # leaving it orphaned in "creating" until the 30-day TTL (which would poison
                # the key and reject every retry). Mirrors the Plivo-rejection handling below.
                logger.exception("Per-call config storage failed while preparing outbound call.")
                await _best_effort_mark_outbound_create_failed(
                    session_store=session_store,
                    idempotency_key=idempotency_key,
                    from_number=from_number,
                    to_number=payload.to,
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Per-call configuration storage is unavailable.",
                ) from exc
            answer_url = _answer_url_with_cfg(answer_url, cfg_id)
        if dry_run:
            request_uuid = f"dryrun-{token_urlsafe(12)}"
            try:
                await app.state.call_session_store.upsert(
                    CallSessionUpdate(
                        idempotency_key=idempotency_key,
                        request_uuid=request_uuid,
                        direction="outbound",
                        from_number=from_number,
                        to_number=payload.to,
                        status="requested",
                        event="outbound_simulated",
                    )
                )
            except Exception as exc:
                logger.exception("Call session storage failed during dry-run outbound.")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Call session storage is unavailable.",
                ) from exc
            logger.info(
                "Outbound dry-run: simulated call to %s (no Plivo request placed).", payload.to
            )
            return JSONResponse(
                status_code=status.HTTP_202_ACCEPTED,
                content={
                    "api_id": "dryrun",
                    "message": "Outbound call simulated (dry-run; no real call placed).",
                    "request_uuid": request_uuid,
                    "dry_run": True,
                },
            )
        # Not dry-run: the guard above guaranteed a real client.
        if call_client is None:
            raise RuntimeError("No outbound call client is configured.")
        callback_error = await _outbound_callback_url_error(
            app=app,
            call_client=call_client,
            answer_url=answer_url,
        )
        if callback_error is not None:
            await _best_effort_mark_outbound_create_failed(
                session_store=session_store,
                idempotency_key=idempotency_key,
                from_number=from_number,
                to_number=payload.to,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    f"Public callback URL is not reachable: {callback_error} "
                    "Refresh VOICE_AGENT_PUBLIC_BASE_URL to the active HTTPS tunnel before "
                    "placing outbound calls."
                ),
            )
        _schedule_outbound_greeting_prewarm(
            settings=resolved_settings,
            per_call_config=per_call_config,
        )
        try:
            result = await call_client.create_call(
                from_number=from_number,
                to_number=payload.to,
                answer_url=answer_url,
                answer_method="POST",
                hangup_url=resolved_settings.status_callback_url,
                hangup_method=resolved_settings.stream_status_callback_method,
                hangup_on_ring_seconds=resolved_settings.outbound_hangup_on_ring_seconds,
                time_limit_seconds=_outbound_time_limit_seconds(
                    resolved_settings,
                    payload.behavior,
                ),
            )
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Plivo rejected outbound call request with status_code=%s response=%s.",
                exc.response.status_code,
                exc.response.text,
            )
            await _best_effort_mark_outbound_create_failed(
                session_store=session_store,
                idempotency_key=idempotency_key,
                from_number=from_number,
                to_number=payload.to,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=_plivo_rejection_detail(exc),
            ) from exc
        except httpx.RequestError as exc:
            logger.warning("Plivo outbound call request failed: %s.", exc)
            await _best_effort_mark_outbound_create_failed(
                session_store=session_store,
                idempotency_key=idempotency_key,
                from_number=from_number,
                to_number=payload.to,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Plivo outbound call request failed.",
            ) from exc
        except PlivoCallClientError as exc:
            logger.warning("Plivo outbound call response was invalid: %s.", exc)
            await _best_effort_mark_outbound_create_failed(
                session_store=session_store,
                idempotency_key=idempotency_key,
                from_number=from_number,
                to_number=payload.to,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Plivo returned an invalid outbound call response.",
            ) from exc
        try:
            await app.state.call_session_store.upsert(
                CallSessionUpdate(
                    idempotency_key=idempotency_key,
                    request_uuid=result.request_uuid,
                    api_id=result.api_id,
                    message=result.message,
                    direction="outbound",
                    from_number=from_number,
                    to_number=payload.to,
                    status="requested",
                    event="outbound_requested",
                )
            )
        except Exception as exc:
            logger.exception("Call session storage failed after Plivo accepted outbound call.")
            await _best_effort_hangup_after_untracked_outbound(
                call_client=call_client,
                request_uuid=result.request_uuid,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Call session storage is unavailable.",
            ) from exc
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "api_id": result.api_id,
                "message": result.message,
                "request_uuid": result.request_uuid,
            },
        )

    @app.get("/calls/{identifier}")
    async def get_call_session(request: Request, identifier: str) -> dict[str, Any]:
        _validate_control_request(request, resolved_settings)
        clean_identifier = _read_control_identifier(identifier)
        session_store = cast(CallSessionStore, app.state.call_session_store)
        try:
            session = await session_store.get(clean_identifier)
        except Exception as exc:
            logger.exception("Call session storage failed while looking up call session.")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Call session storage is unavailable.",
            ) from exc
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Call not found.")
        return session

    @app.post("/preview/turn")
    async def preview_turn(request: Request) -> JSONResponse:
        _validate_control_request(request, resolved_settings)
        _enforce_control_rate_limit(request, app)
        payload = await _read_preview_turn_request(
            request,
            max_body_bytes=resolved_settings.control_max_body_bytes,
        )
        try:
            raw_reply = await generate_preview_reply(
                settings=resolved_settings,
                call_id=f"preview-{token_urlsafe(8)}",
                user_text=payload.user_text,
                llm_provider_override=payload.provider,
                llm_model_override=payload.model,
                llm_system_prompt_override=payload.system_prompt,
                temperature_override=payload.temperature,
            )
        except Exception as exc:
            logger.warning("Preview LLM generation failed.", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Preview generation failed.",
            ) from exc
        end_call = END_CALL_MARKER in raw_reply
        reply = raw_reply.replace(END_CALL_MARKER, "").strip()
        behavior = payload.behavior or {}
        closing_message = behavior.get("closing_message") if end_call else None
        return JSONResponse(
            content={
                "user_text": payload.user_text,
                "reply": reply,
                "end_call": end_call,
                "closing_message": (
                    closing_message.strip()
                    if isinstance(closing_message, str) and closing_message.strip()
                    else None
                ),
            }
        )

    @app.post("/preview/tts")
    async def preview_tts(request: Request) -> JSONResponse:
        _validate_control_request(request, resolved_settings)
        _enforce_control_rate_limit(request, app)
        payload = await _read_preview_tts_request(
            request,
            max_body_bytes=resolved_settings.control_max_body_bytes,
        )
        provider = payload.provider.strip().lower() if payload.provider else None
        deployed_provider = resolved_settings.tts_provider.strip().lower()
        if provider is not None and provider != deployed_provider:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"TTS preview provider '{provider}' does not match the deployed "
                    f"call TTS provider '{deployed_provider}'."
                ),
            )
        try:
            audio = await generate_tts_preview_audio(
                settings=resolved_settings,
                text=payload.text,
                tts_override=payload.tts,
                audio_format=PlivoAudioFormat.L16_16KHZ,
            )
            preview_audio = _pcm16_16khz_to_wav(audio)
        except Exception as exc:
            logger.warning("Preview TTS generation failed.", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="TTS preview generation failed.",
            ) from exc
        return JSONResponse(
            content={
                "engine": deployed_provider,
                "mime": "audio/wav",
                "source_mime": "audio/L16;rate=16000",
                "audio_base64": base64.b64encode(preview_audio).decode("ascii"),
            }
        )

    @app.get("/latency/summary")
    async def get_latency_summary(
        request: Request,
        limit: int = Query(default=100, ge=1, le=1000),
        call_id: str | None = Query(default=None, min_length=1, max_length=128),
    ) -> dict[str, Any]:
        _validate_control_request(request, resolved_settings)
        clean_call_id = _read_control_identifier(call_id) if call_id is not None else None
        event_store = cast(CallEventStore, app.state.call_event_store)
        try:
            events = await event_store.recent(count=limit)
        except Exception as exc:
            logger.exception("Call event storage failed while reading latency summary.")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Call event storage is unavailable.",
            ) from exc
        return summarize_voice_turn_latency_events(events, call_id=clean_call_id)

    @app.delete("/calls/{identifier}")
    async def hangup_call(request: Request, identifier: str) -> JSONResponse:
        _validate_control_request(request, resolved_settings)
        clean_identifier = _read_control_identifier(identifier)
        session_store = cast(CallSessionStore, app.state.call_session_store)
        try:
            session = await session_store.get(clean_identifier)
        except Exception as exc:
            logger.exception("Call session storage failed while loading call session for hangup.")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Call session storage is unavailable.",
            ) from exc
        if session is None:
            call_client = app.state.outbound_call_client
            if call_client is None or not _is_direct_plivo_hangup_identifier(clean_identifier):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Call not found.")
            try:
                await call_client.hangup_call(clean_identifier)
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "Plivo rejected direct hangup request with status_code=%s response=%s.",
                    exc.response.status_code,
                    exc.response.text,
                )
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Plivo rejected the hangup request.",
                ) from exc
            except httpx.RequestError as exc:
                logger.warning("Direct Plivo hangup request failed: %s.", exc)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Plivo hangup request failed.",
                ) from exc
            except PlivoCallClientError as exc:
                logger.warning("Direct Plivo hangup response was invalid: %s.", exc)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Plivo returned an invalid hangup response.",
                ) from exc
            return JSONResponse(
                status_code=status.HTTP_202_ACCEPTED,
                content={
                    "status": "hangup_requested",
                    "identifier": clean_identifier,
                    "plivo_identifier": clean_identifier,
                },
            )
        if is_terminal_call_status(session.get("status")):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Tracked call session is already terminal.",
            )
        call_client = app.state.outbound_call_client
        if call_client is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Outbound call control is not configured.",
            )
        plivo_identifier = _select_plivo_hangup_identifier(session)
        try:
            await call_client.hangup_call(plivo_identifier)
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Plivo rejected hangup request with status_code=%s response=%s.",
                exc.response.status_code,
                exc.response.text,
            )
            await _best_effort_mark_control_hangup_failed(
                session_store=session_store,
                session=session,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Plivo rejected the hangup request.",
            ) from exc
        except httpx.RequestError as exc:
            logger.warning("Plivo hangup request failed: %s.", exc)
            await _best_effort_mark_control_hangup_failed(
                session_store=session_store,
                session=session,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Plivo hangup request failed.",
            ) from exc
        except PlivoCallClientError as exc:
            logger.warning("Plivo hangup response was invalid: %s.", exc)
            await _best_effort_mark_control_hangup_failed(
                session_store=session_store,
                session=session,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Plivo returned an invalid hangup response.",
            ) from exc
        try:
            await _mark_control_hangup_requested(
                session_store=session_store,
                session=session,
                plivo_identifier=plivo_identifier,
            )
        except Exception as exc:
            logger.exception("Call session storage failed after Plivo accepted hangup request.")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Call session storage is unavailable.",
            ) from exc
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "status": "hangup_requested",
                "identifier": clean_identifier,
                "plivo_identifier": plivo_identifier,
            },
        )

    @app.websocket("/plivo/media")
    async def plivo_media(websocket: WebSocket) -> None:
        # Authorize the media stream by the agent's own signed capability token (the `token`
        # in the URL, bound to call_id + cfg) when stream_auth_token is configured — this is
        # the reliable, deployment-controlled mechanism. Only fall back to validating Plivo's
        # request signature when no stream token is configured (Plivo's WS-URL signature is
        # fragile to reconstruct behind proxies/tunnels).
        if getattr(app.state, "shutting_down", False):
            # The process is draining for shutdown; the load balancer should already have
            # stopped routing here (via /readyz). Refuse new calls defensively rather than
            # accept one we are about to tear down.
            await websocket.close(code=1013, reason="server shutting down")
            return
        stream_claims = _verify_stream_token_at_connect(websocket, resolved_settings)
        if resolved_settings.stream_auth_token is None:
            if not _is_valid_plivo_websocket_upgrade(websocket, resolved_settings):
                await websocket.close(code=1008, reason="invalid Plivo signature")
                return
        elif not _is_allowed_stream_claims(stream_claims, resolved_settings):
            await websocket.close(code=1008, reason="invalid stream token")
            return
        if not await _claim_stream_token_single_use(app, resolved_settings, stream_claims):
            await websocket.close(code=1008, reason="stream token already used")
            return
        await websocket.accept()
        runner = None
        last_data: dict[str, Any] | None = None
        try:
            while True:
                data = await _receive_plivo_media_json(
                    websocket,
                    start_timeout_seconds=(
                        resolved_settings.media_start_timeout_seconds if runner is None else None
                    ),
                    max_message_bytes=resolved_settings.media_message_max_bytes,
                )
                last_data = data
                parsed = parse_plivo_message(data, audio_format=PlivoAudioFormat.L16_16KHZ)

                if runner is None and parsed.kind != "start":
                    if stream_claims is not None:
                        await _best_effort_mark_claimed_media_protocol_error(
                            session_store=app.state.call_session_store,
                            stream_claims=stream_claims,
                            data=data,
                        )
                    await websocket.close(code=1008, reason="event received before start")
                    return

                if parsed.kind == "start":
                    if runner is not None:
                        await _best_effort_mark_media_protocol_error(
                            session_store=app.state.call_session_store,
                            runner=runner,
                        )
                        await websocket.close(code=1008, reason="duplicate start event")
                        return
                    call_id, stream_id = extract_start_identifiers(data)
                    if not _is_valid_start_for_stream_claims(
                        stream_claims,
                        resolved_settings,
                        call_id,
                    ):
                        if stream_claims is not None:
                            await _best_effort_mark_media_session(
                                session_store=app.state.call_session_store,
                                call_id=stream_claims.call_id,
                                stream_id=stream_id,
                                status="media_failed",
                                event="media_token_mismatch",
                            )
                        await websocket.close(code=1008, reason="invalid stream token")
                        return
                    # Lifecycle bookkeeping must never gate the realtime media path: a
                    # transient Redis blip on this non-critical write would otherwise drop a
                    # live call (the broad handler below closes the socket with 1011). The
                    # audio session is what matters, so record best-effort and keep going.
                    await _best_effort_mark_media_session(
                        session_store=app.state.call_session_store,
                        call_id=call_id,
                        stream_id=stream_id,
                        status="media_started",
                        event="media_start",
                    )
                    config_store = cast(PerCallConfigStore, app.state.per_call_config_store)
                    cfg_id = websocket.query_params.get("cfg")
                    per_call_config = await config_store.get(cfg_id) if cfg_id else None
                    try:
                        runner = await build_media_session_runner(
                            settings=resolved_settings,
                            call_id=call_id,
                            stream_id=stream_id,
                            transport=websocket,
                            llm_provider_override=(
                                per_call_config.provider if per_call_config else None
                            ),
                            llm_model_override=(per_call_config.model if per_call_config else None),
                            llm_system_prompt_override=(
                                per_call_config.system_prompt if per_call_config else None
                            ),
                            greeting_override=(
                                per_call_config.greeting if per_call_config else None
                            ),
                            temperature_override=(
                                per_call_config.temperature if per_call_config else None
                            ),
                            behavior_override=(
                                per_call_config.behavior if per_call_config else None
                            ),
                            stt_override=(per_call_config.stt if per_call_config else None),
                            tts_override=(per_call_config.tts if per_call_config else None),
                            latency_observer=_build_voice_turn_latency_observer(
                                cast(CallEventStore, app.state.call_event_store),
                                cast(MetricsRegistry, app.state.metrics),
                            ),
                            transcript_observer=_build_call_transcript_observer(
                                app.state.call_session_store,
                                call_id=call_id,
                                interval_seconds=(
                                    resolved_settings.transcript_persist_interval_seconds
                                ),
                            ),
                            voice_action_observer=_build_voice_action_observer(
                                cast(CallEventStore, app.state.call_event_store)
                            ),
                            hangup_observer=_build_agent_hangup_observer(
                                app.state.outbound_call_client,
                                app.state.call_session_store,
                            ),
                        )
                    except Exception:
                        await _best_effort_mark_media_session(
                            session_store=app.state.call_session_store,
                            call_id=call_id,
                            stream_id=stream_id,
                            status="media_failed",
                            event="media_start_failed",
                        )
                        raise
                    await runner.start()
                    # Track the live call so a graceful shutdown can drain it before the
                    # storage clients are closed.
                    _register_media_runner(app, runner)
                    await runner.greet()
                    continue

                if (
                    runner is not None
                    and parsed.stream_id is not None
                    and parsed.stream_id != runner.stream_id
                ):
                    await _best_effort_mark_media_protocol_error(
                        session_store=app.state.call_session_store,
                        runner=runner,
                    )
                    await websocket.close(code=1008, reason="stream id mismatch")
                    return

                if parsed.kind == "media":
                    if runner is None:
                        if stream_claims is not None:
                            await _best_effort_mark_media_session(
                                session_store=app.state.call_session_store,
                                call_id=stream_claims.call_id,
                                stream_id=parsed.stream_id,
                                status="media_failed",
                                event="media_protocol_error",
                            )
                        await websocket.close(code=1008, reason="media received before start")
                        return
                    if parsed.audio is None:
                        await _best_effort_mark_media_protocol_error(
                            session_store=app.state.call_session_store,
                            runner=runner,
                        )
                        await websocket.close(code=1008, reason="media received before start")
                        return
                    await runner.accept_audio(parsed.audio)
                    continue

                if parsed.kind == "dtmf":
                    if runner is None:
                        if stream_claims is not None:
                            await _best_effort_mark_claimed_media_protocol_error(
                                session_store=app.state.call_session_store,
                                stream_claims=stream_claims,
                                data=data,
                            )
                        await websocket.close(code=1008, reason="dtmf received before start")
                        return
                    continue

                if parsed.kind == "playedStream":
                    # Plivo's authoritative playback-finished ack for a checkpoint; let the
                    # runner end its local playback estimate early (no-op unless enabled).
                    if runner is not None:
                        runner.notify_played_stream(parsed.name)
                    continue

                if parsed.kind == "stop":
                    if runner is not None:
                        await _best_effort_mark_active_media_session(
                            session_store=app.state.call_session_store,
                            runner=runner,
                            status="media_stopped",
                            event="media_stop",
                        )
                    elif stream_claims is not None:
                        await _best_effort_mark_claimed_media_protocol_error(
                            session_store=app.state.call_session_store,
                            stream_claims=stream_claims,
                            data=data,
                        )
                    return
        except WebSocketDisconnect:
            if runner is not None:
                await _best_effort_mark_active_media_session(
                    session_store=app.state.call_session_store,
                    runner=runner,
                    status="media_disconnected",
                    event="media_disconnect",
                )
            return
        except TimeoutError:
            if stream_claims is not None:
                await _best_effort_mark_media_session(
                    session_store=app.state.call_session_store,
                    call_id=stream_claims.call_id,
                    stream_id=None,
                    status="media_failed",
                    event="media_start_timeout",
                )
            await websocket.close(code=1008, reason="start event timeout")
        except PlivoProtocolError:
            if runner is not None:
                await _best_effort_mark_media_protocol_error(
                    session_store=app.state.call_session_store,
                    runner=runner,
                )
            elif stream_claims is not None:
                await _best_effort_mark_claimed_media_protocol_error(
                    session_store=app.state.call_session_store,
                    stream_claims=stream_claims,
                    data=last_data or {},
                )
            await websocket.close(code=1008, reason="invalid Plivo media message")
        except Exception:
            logger.exception("Plivo media WebSocket processing failed.")
            if runner is not None:
                await _best_effort_mark_active_media_session(
                    session_store=app.state.call_session_store,
                    runner=runner,
                    status="media_failed",
                    event="media_processing_failed",
                )
            with suppress(Exception):
                await websocket.close(code=1011, reason="media processing failed")
        finally:
            if runner is not None:
                _deregister_media_runner(app, runner)
                await _best_effort_close_media_runner(runner)

    return app


async def _run_readiness_check(
    name: str,
    check: Awaitable[bool],
    *,
    timeout_seconds: float,
) -> bool:
    try:
        return bool(await asyncio.wait_for(check, timeout=timeout_seconds))
    except TimeoutError:
        logger.warning(
            "Readiness check timed out for %s after %.2fs.",
            name,
            timeout_seconds,
        )
        return False
    except Exception:
        logger.exception("Readiness check failed for %s.", name)
        return False


def _missing_local_speech_model_files(settings: Settings) -> list[str] | None:
    """Missing model files for the CPU-local speech providers, or None if unused.

    Cheap stat-only check so /readyz reports a bad model deployment before a call
    fails mid-stream; engine loadability is still proven by the providers themselves.
    """
    if settings.llm_provider == "google_live":
        # google_live handles speech itself; the STT/TTS providers are bypassed
        # (mirrors Settings.validate_runtime_safety).
        return None
    missing: list[str] = []
    required: list[Path] = []
    if settings.stt_provider == "sherpa":
        model_dir = Path(settings.sherpa_stt_model_dir)
        required.append(model_dir / "tokens.txt")
        for component in ("encoder", "decoder", "joiner"):
            if not any(model_dir.glob(f"{component}*.onnx")):
                missing.append(str(model_dir / f"{component}*.onnx"))
        required.append(Path(settings.sherpa_stt_vad_model_path))
        if settings.sherpa_stt_smart_turn_model_path:
            required.append(Path(settings.sherpa_stt_smart_turn_model_path))
    if settings.stt_provider == "mlx-whisper":
        required.append(Path(settings.sherpa_stt_vad_model_path))
        if settings.sherpa_stt_smart_turn_model_path:
            required.append(Path(settings.sherpa_stt_smart_turn_model_path))
        whisper = Path(settings.mlx_whisper_model)
        if whisper.is_absolute():  # a local model directory (CareFlow); a repo id is not checked
            required.extend((whisper / "config.json", whisper / "weights.safetensors"))
    if settings.tts_provider == "kokoro":
        required.append(Path(settings.kokoro_model_path))
        required.append(Path(settings.kokoro_voices_path))
    if not required and not missing:
        return None
    missing.extend(str(path) for path in required if not path.is_file())
    return missing


async def _luxtts_sidecar_readiness(settings: Settings) -> tuple[bool, dict[str, object]]:
    try:
        return await asyncio.wait_for(
            _fetch_luxtts_sidecar_readiness(settings),
            timeout=settings.readiness_check_timeout_seconds,
        )
    except TimeoutError:
        logger.warning(
            "LuxTTS sidecar readiness timed out after %.2fs.",
            settings.readiness_check_timeout_seconds,
        )
        return False, {"status": "timeout"}
    except Exception as exc:
        logger.warning("LuxTTS sidecar readiness failed: %s.", exc)
        return False, {"status": "error", "error": exc.__class__.__name__}


async def _fetch_luxtts_sidecar_readiness(
    settings: Settings,
) -> tuple[bool, dict[str, object]]:
    if not settings.luxtts_tts_url:
        return False, {"status": "missing_url"}
    readyz_url = _luxtts_readyz_url(settings.luxtts_tts_url)
    headers = {"accept": "application/json"}
    if settings.luxtts_api_key is not None:
        headers["authorization"] = f"Bearer {settings.luxtts_api_key.get_secret_value()}"
    timeout = httpx.Timeout(settings.readiness_check_timeout_seconds)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(readyz_url, headers=headers)
    except httpx.HTTPError as exc:
        return False, {"status": "request_error", "error": exc.__class__.__name__}
    if response.status_code != 200:
        return False, {"status": "http_error", "http_status": response.status_code}
    try:
        payload: object = response.json()
    except ValueError:
        return False, {"status": "invalid_json"}
    if not isinstance(payload, dict):
        return False, {"status": "invalid_payload"}
    payload_dict = {str(key): value for key, value in payload.items()}
    detail = _sanitize_luxtts_sidecar_readyz(payload_dict)
    is_ready = payload_dict.get("ok") is True and payload_dict.get("loaded") is True
    detail["status"] = "ready" if is_ready else "not_ready"
    return is_ready, detail


def _luxtts_readyz_url(tts_url: str) -> str:
    parsed = urlsplit(tts_url)
    return parsed._replace(path="/readyz", query="", fragment="").geturl()


def _sanitize_luxtts_sidecar_readyz(payload: dict[str, object]) -> dict[str, object]:
    allowed_keys = (
        "ok",
        "loaded",
        "sidecar_instance_id",
        "sidecar_source_sha256",
        "device",
        "requested_device",
        "model",
        "model_repo_id",
        "model_revision",
        "model_artifact_sha256",
        "model_artifact_file_count",
        "voice",
        "active_synthesis_requests",
        "synthesis_queue_depth",
        "max_queue_depth",
        "synthesis_capacity",
        "accepting_tts_requests",
        "lux_repo_git_available",
        "lux_repo_commit",
        "lux_repo_dirty",
        "lux_repo_remote_url",
    )
    return {key: payload[key] for key in allowed_keys if key in payload}


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        yield
    finally:
        # Stop accepting new calls (/readyz -> not_ready) and let in-flight media calls
        # drain before the storage clients are torn out from under them.
        app.state.shutting_down = True
        settings = cast(Settings | None, getattr(app.state, "settings", None))
        grace_seconds = settings.shutdown_grace_seconds if settings is not None else 0.0
        try:
            await _drain_active_media_runners(app, grace_seconds=grace_seconds)
        except Exception:
            logger.exception("Error while draining active media calls during shutdown.")
        try:
            await app.state.call_event_store.close()
        except Exception:
            logger.exception("Error while closing call event store.")
        try:
            await app.state.call_session_store.close()
        except Exception:
            logger.exception("Error while closing call session store.")
        try:
            await app.state.per_call_config_store.close()
        except Exception:
            logger.exception("Error while closing per-call config store.")
        nonce_store = getattr(app.state, "nonce_store", None)
        if nonce_store is not None:
            try:
                await nonce_store.close()
            except Exception:
                logger.exception("Error while closing nonce store.")
        outbound_call_client = getattr(app.state, "outbound_call_client", None)
        close = getattr(outbound_call_client, "close", None)
        if close is not None:
            try:
                await close()
            except Exception:
                logger.exception("Error while closing outbound call client.")


def _register_media_runner(app: FastAPI, runner: ActiveMediaRunner) -> None:
    runners = cast("set[ActiveMediaRunner]", app.state.active_media_runners)
    runners.add(runner)
    cast(asyncio.Event, app.state.media_drained).clear()


def _deregister_media_runner(app: FastAPI, runner: ActiveMediaRunner) -> None:
    runners = cast("set[ActiveMediaRunner]", app.state.active_media_runners)
    runners.discard(runner)
    if not runners:
        cast(asyncio.Event, app.state.media_drained).set()


async def _drain_active_media_runners(app: FastAPI, *, grace_seconds: float) -> None:
    """Wait up to grace_seconds for in-flight media calls to finish on their own, then
    force-close any stragglers so their provider clients are released cleanly. Runners
    deregister themselves (setting media_drained when the last one finishes) as their
    sockets close."""
    runners = cast("set[ActiveMediaRunner]", getattr(app.state, "active_media_runners", set()))
    if not runners:
        return
    drained = cast(asyncio.Event, app.state.media_drained)
    drained.clear()
    if not runners:
        return
    with suppress(TimeoutError):
        await asyncio.wait_for(drained.wait(), timeout=max(grace_seconds, 0.0))
    if runners:
        logger.warning(
            "Shutdown grace elapsed with %d active media call(s) still running; closing them.",
            len(runners),
        )
        for runner in list(runners):
            await _best_effort_close_media_runner(runner)
        runners.clear()
        drained.set()


async def _best_effort_mark_media_session(
    *,
    session_store: CallSessionStore,
    call_id: str,
    stream_id: str | None,
    status: str,
    event: str,
) -> None:
    try:
        await session_store.upsert(
            CallSessionUpdate(
                call_id=call_id,
                stream_id=stream_id,
                status=status,
                event=event,
            )
        )
    except Exception:
        logger.exception("Call session storage failed while marking media lifecycle event.")


async def _best_effort_close_media_runner(runner: ActiveMediaRunner) -> None:
    try:
        await runner.close()
    except Exception:
        logger.exception(
            "Error while closing media session runner call_id=%s stream_id=%s.",
            runner.call_id,
            runner.stream_id,
        )


async def _best_effort_mark_media_protocol_error(
    *,
    session_store: CallSessionStore,
    runner: ActiveMediaRunner,
) -> None:
    await _best_effort_mark_active_media_session(
        session_store=session_store,
        runner=runner,
        status="media_failed",
        event="media_protocol_error",
    )


async def _best_effort_mark_claimed_media_protocol_error(
    *,
    session_store: CallSessionStore,
    stream_claims: StreamTokenClaims,
    data: dict[str, Any],
) -> None:
    await _best_effort_mark_media_session(
        session_store=session_store,
        call_id=stream_claims.call_id,
        stream_id=_extract_possible_stream_id(data),
        status="media_failed",
        event="media_protocol_error",
    )


async def _best_effort_mark_active_media_session(
    *,
    session_store: CallSessionStore,
    runner: ActiveMediaRunner,
    status: str,
    event: str,
) -> None:
    await _best_effort_mark_media_session(
        session_store=session_store,
        call_id=runner.call_id,
        stream_id=runner.stream_id,
        status=status,
        event=event,
    )


def _extract_possible_stream_id(data: dict[str, Any]) -> str | None:
    stream_id = _first_callback_string(
        data,
        "streamId",
        "stream_id",
        "streamSid",
        "stream_sid",
        "streamUuid",
        "stream_uuid",
    )
    if stream_id is not None:
        return _safe_possible_stream_id(stream_id)
    start = data.get("start")
    if not isinstance(start, dict):
        return None
    return _safe_possible_stream_id(
        _first_callback_string(
            start,
            "stream_id",
            "streamId",
            "stream_sid",
            "streamSid",
            "stream_uuid",
            "streamUuid",
        )
    )


def _safe_possible_stream_id(stream_id: str | None) -> str | None:
    if stream_id is None:
        return None
    if CONTROL_IDENTIFIER_RE.fullmatch(stream_id) is None:
        return None
    return stream_id


async def _read_outbound_call_request(
    request: Request,
    *,
    max_body_bytes: int,
) -> OutboundCallRequest:
    _reject_oversized_control_content_length(request, max_body_bytes=max_body_bytes)
    body = await _read_limited_control_body(request, max_body_bytes=max_body_bytes)
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Control request payload must be valid JSON.",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Control request payload must be a JSON object.",
        )
    try:
        return OutboundCallRequest.model_validate(payload)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


async def _read_preview_turn_request(
    request: Request,
    *,
    max_body_bytes: int,
) -> PreviewTurnRequest:
    _reject_oversized_control_content_length(request, max_body_bytes=max_body_bytes)
    body = await _read_limited_control_body(request, max_body_bytes=max_body_bytes)
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Control request payload must be valid JSON.",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Control request payload must be a JSON object.",
        )
    try:
        return PreviewTurnRequest.model_validate(payload)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


async def _read_preview_tts_request(
    request: Request,
    *,
    max_body_bytes: int,
) -> PreviewTTSRequest:
    _reject_oversized_control_content_length(request, max_body_bytes=max_body_bytes)
    body = await _read_limited_control_body(request, max_body_bytes=max_body_bytes)
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Control request payload must be valid JSON.",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Control request payload must be a JSON object.",
        )
    try:
        return PreviewTTSRequest.model_validate(payload)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


def _reject_oversized_control_content_length(
    request: Request,
    *,
    max_body_bytes: int,
) -> None:
    content_length = request.headers.get("content-length")
    if content_length is None:
        return
    try:
        body_size = int(content_length)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Content-Length header must be an integer.",
        ) from exc
    if body_size < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Content-Length header must be non-negative.",
        )
    if body_size > max_body_bytes:
        raise _control_payload_too_large()


async def _read_limited_control_body(request: Request, *, max_body_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total_body_bytes = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        total_body_bytes += len(chunk)
        if total_body_bytes > max_body_bytes:
            raise _control_payload_too_large()
        chunks.append(chunk)
    return b"".join(chunks)


def _control_payload_too_large() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail="Control request payload exceeds configured size limit.",
    )


def _pcm16_16khz_to_wav(audio: bytes) -> bytes:
    if len(audio) % 2:
        raise ValueError("PCM16 preview audio has an incomplete sample.")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(audio)
    return buffer.getvalue()


async def _read_callback_payload(request: Request, *, max_body_bytes: int) -> dict[str, Any]:
    if request.method == "GET":
        _reject_oversized_callback_query(request, max_body_bytes=max_body_bytes)
        try:
            return _decode_query_callback_payload(request)
        except DuplicateCallbackFieldError as exc:
            raise _duplicate_callback_field_error() from exc
        except InvalidCallbackFieldNameError as exc:
            raise _invalid_callback_field_name_error() from exc
        except UnsafeCallbackFieldNameError as exc:
            raise _unsafe_callback_field_name_error() from exc
    _reject_oversized_callback_content_length(request, max_body_bytes=max_body_bytes)
    body = await _read_limited_callback_body(request, max_body_bytes=max_body_bytes)
    content_type = request.headers.get("content-type", "")
    if _is_json_content_type(content_type):
        try:
            payload_text = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Callback payload must be valid UTF-8 JSON.",
            ) from exc
        try:
            payload = json.loads(
                payload_text,
                object_pairs_hook=_unique_callback_pairs_to_dict,
            )
        except DuplicateCallbackFieldError as exc:
            raise _duplicate_callback_field_error() from exc
        except InvalidCallbackFieldNameError as exc:
            raise _invalid_callback_field_name_error() from exc
        except UnsafeCallbackFieldNameError as exc:
            raise _unsafe_callback_field_name_error() from exc
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Callback payload must be valid JSON.",
            ) from exc
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Callback payload must be a JSON object.",
            )
        _validate_callback_field_values(payload)
        return payload
    try:
        return _decode_form_callback_payload(body)
    except DuplicateCallbackFieldError as exc:
        raise _duplicate_callback_field_error() from exc
    except InvalidCallbackFieldNameError as exc:
        raise _invalid_callback_field_name_error() from exc
    except UnsafeCallbackFieldNameError as exc:
        raise _unsafe_callback_field_name_error() from exc


def _decode_query_callback_payload(request: Request) -> dict[str, str]:
    query_string = request.scope.get("query_string", b"")
    if isinstance(query_string, bytes):
        try:
            query_payload = query_string.decode("ascii")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Callback query payload must be ASCII.",
            ) from exc
    else:
        query_payload = str(query_string)
    return _unique_callback_pairs_to_dict(parse_qsl(query_payload, keep_blank_values=True))


def _is_json_content_type(content_type: str) -> bool:
    media_type = content_type.split(";", maxsplit=1)[0].strip().lower()
    return media_type == "application/json"


def _reject_oversized_callback_query(request: Request, *, max_body_bytes: int) -> None:
    query_string = request.scope.get("query_string", b"")
    query_size = (
        len(query_string)
        if isinstance(query_string, bytes)
        else len(str(query_string).encode("utf-8"))
    )
    if query_size > max_body_bytes:
        raise _callback_payload_too_large()


def _reject_oversized_callback_content_length(
    request: Request,
    *,
    max_body_bytes: int,
) -> None:
    content_length = request.headers.get("content-length")
    if content_length is None:
        return
    try:
        body_size = int(content_length)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Content-Length header must be an integer.",
        ) from exc
    if body_size < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Content-Length header must be non-negative.",
        )
    if body_size > max_body_bytes:
        raise _callback_payload_too_large()


async def _read_limited_callback_body(request: Request, *, max_body_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total_body_bytes = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        total_body_bytes += len(chunk)
        if total_body_bytes > max_body_bytes:
            raise _callback_payload_too_large()
        chunks.append(chunk)
    return b"".join(chunks)


def _decode_form_callback_payload(body: bytes) -> dict[str, str]:
    if not body:
        return {}
    try:
        payload = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Callback form payload must be UTF-8.",
        ) from exc
    return _unique_callback_pairs_to_dict(parse_qsl(payload, keep_blank_values=True))


def _unique_callback_pairs_to_dict(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if not key.strip():
            raise InvalidCallbackFieldNameError("Callback payload field names must not be blank.")
        if CALLBACK_FIELD_NAME_RE.fullmatch(key) is None:
            raise UnsafeCallbackFieldNameError(
                "Callback payload field names contain unsafe characters."
            )
        if key in payload:
            raise DuplicateCallbackFieldError("Callback payload fields must be unique.")
        payload[key] = value
    return payload


def _validate_callback_field_values(payload: dict[str, Any]) -> None:
    if not all(isinstance(value, str) for value in payload.values()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Callback payload field values must be strings.",
        )


def _duplicate_callback_field_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Callback payload fields must be unique.",
    )


def _invalid_callback_field_name_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Callback payload field names must not be blank.",
    )


def _unsafe_callback_field_name_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Callback payload field names contain unsafe characters.",
    )


def _callback_payload_too_large() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail="Callback payload exceeds configured size limit.",
    )


async def _receive_plivo_media_json(
    websocket: WebSocket,
    *,
    start_timeout_seconds: float | None,
    max_message_bytes: int,
) -> dict[str, Any]:
    if start_timeout_seconds is None:
        raw_message = await websocket.receive()
    else:
        raw_message = await asyncio.wait_for(
            websocket.receive(),
            timeout=start_timeout_seconds,
        )
    if raw_message.get("type") == "websocket.disconnect":
        raise WebSocketDisconnect(
            code=int(raw_message.get("code") or 1000),
            reason=str(raw_message.get("reason") or ""),
        )
    message_text = raw_message.get("text")
    if not isinstance(message_text, str):
        raise PlivoProtocolError("Plivo WebSocket message must be a text JSON frame.")
    if len(message_text.encode("utf-8")) > max_message_bytes:
        raise PlivoProtocolError("Plivo WebSocket message exceeds configured size limit.")
    try:
        message = json.loads(message_text)
    except json.JSONDecodeError as exc:
        raise PlivoProtocolError("Plivo WebSocket message must be valid JSON.") from exc
    if not isinstance(message, dict):
        raise PlivoProtocolError("Plivo WebSocket message must be a JSON object.")
    return message


def _validate_plivo_request(
    request: Request,
    settings: Settings,
    params: dict[str, Any],
    *,
    fatal: bool = True,
) -> bool:
    """Validate a Plivo callback's V3 signature. Returns True when valid. When fatal
    (the default, used by /plivo/answer which controls the call) an invalid signature
    raises 403; callers that must never abort the live call (the informational status
    callback) pass fatal=False and handle the False return themselves."""
    if not settings.should_validate_plivo_signature:
        return True
    auth_token = settings.plivo_auth_token
    if auth_token is None:
        if fatal:
            raise HTTPException(
                status_code=403, detail="Plivo signature validation is not configured."
            )
        return False

    string_params = {key: str(value) for key, value in params.items()}
    https_url = settings.plivo_base_url + request.url.path
    if request.url.query:
        https_url = f"{https_url}?{request.url.query}"
    nonce = request.headers.get("X-Plivo-Signature-V3-Nonce")
    signature = request.headers.get("X-Plivo-Signature-V3")
    main_signature = request.headers.get("X-Plivo-Signature-Ma-V3")
    # Call callbacks (e.g. /plivo/answer) sign {url}.{sorted_params}.{nonce}; Plivo's
    # media-stream status callbacks sign {url}.{nonce} with NO params (the same form as
    # the media WS upgrade). Accept either form, across the https/wss URL representations,
    # so a genuine Plivo callback is never rejected (a 403 here makes Plivo abort the call).
    is_valid = validate_v3_signature(
        method=request.method,
        url=https_url,
        nonce=nonce,
        auth_token=auth_token.get_secret_value(),
        signature_header=signature,
        main_account_signature_header=main_signature,
        params=string_params,
    )
    if not is_valid:
        wss_url = https_url.replace("https://", "wss://", 1)
        is_valid = any(
            validate_websocket_v3_signature(
                url=candidate,
                nonce=nonce,
                auth_token=auth_token.get_secret_value(),
                signature_header=signature,
                main_account_signature_header=main_signature,
            )
            for candidate in (https_url, wss_url)
        )
    if not is_valid and fatal:
        raise HTTPException(status_code=403, detail="Invalid Plivo signature.")
    return is_valid


def _validate_stream_status_callback_method(request: Request, settings: Settings) -> None:
    if request.method != settings.stream_status_callback_method:
        raise HTTPException(
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            detail=(
                f"Plivo status callback method must be {settings.stream_status_callback_method}."
            ),
        )


def _validate_control_request(request: Request, settings: Settings) -> None:
    if settings.control_api_key is None:
        if settings.environment == "production":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Control API authentication is not configured.",
            )
        return
    supplied_authorization = request.headers.get("Authorization", "")
    expected_authorization = f"Bearer {settings.control_api_key.get_secret_value()}"
    try:
        supplied_authorization.encode("ascii")
    except UnicodeEncodeError as exc:
        raise _invalid_control_credentials() from exc
    if not compare_digest(supplied_authorization, expected_authorization):
        raise _invalid_control_credentials()


async def _claim_stream_token_single_use(
    app: FastAPI,
    settings: Settings,
    claims: StreamTokenClaims | None,
) -> bool:
    """Return True if the connection may proceed. When single-use is enabled, claim the
    token nonce and return False on a replay. Fails open (returns True) if the nonce store
    is unavailable so a backing-store blip never drops live calls."""
    if not settings.stream_token_single_use or claims is None or not claims.nonce:
        return True
    store = cast(NonceStore, app.state.nonce_store)
    ttl_seconds = max(claims.expires_at - int(time.time()), 1)
    try:
        return await store.claim(f"stream:{claims.nonce}", ttl_seconds=ttl_seconds)
    except Exception:
        logger.warning("Stream-token nonce store unavailable; allowing connect.", exc_info=True)
        return True


async def _enforce_webhook_replay_protection(
    app: FastAPI,
    settings: Settings,
    request: Request,
) -> None:
    if not settings.webhook_replay_protection:
        return
    nonce = request.headers.get("X-Plivo-Signature-V3-Nonce")
    if not nonce:
        return
    store = cast(NonceStore, app.state.nonce_store)
    try:
        claimed = await store.claim(
            f"webhook:{nonce}", ttl_seconds=settings.webhook_nonce_ttl_seconds
        )
    except Exception:
        logger.warning("Webhook nonce store unavailable; allowing callback.", exc_info=True)
        return
    if not claimed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Duplicate Plivo callback rejected (replay protection).",
        )


def _enforce_control_rate_limit(request: Request, app: FastAPI) -> None:
    limiter = cast(
        SlidingWindowRateLimiter | None,
        getattr(app.state, "control_rate_limiter", None),
    )
    if limiter is None:
        return
    if not limiter.allow(_rate_limit_key(request)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Control API rate limit exceeded.",
            headers={"Retry-After": "60"},
        )


def _rate_limit_key(request: Request) -> str:
    authorization = request.headers.get("Authorization")
    if authorization:
        # Key by a hash of the credential (never the raw secret) so each control client gets
        # its own window without retaining the secret as a dict key.
        return "auth:" + hashlib.sha256(authorization.encode("utf-8")).hexdigest()
    client = request.client
    return f"ip:{client.host}" if client is not None else "ip:unknown"


def _invalid_control_credentials() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid control API credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _read_idempotency_key(request: Request) -> str:
    idempotency_key = request.headers.get("Idempotency-Key")
    if idempotency_key is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key header is required for outbound calls.",
        )
    clean_idempotency_key = idempotency_key.strip()
    if not clean_idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key header is required for outbound calls.",
        )
    if not IDEMPOTENCY_KEY_RE.fullmatch(clean_idempotency_key):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Idempotency-Key must be 8-128 characters using letters, numbers, "
                "dot, underscore, colon, or hyphen."
            ),
        )
    return clean_idempotency_key


def _read_control_identifier(identifier: str) -> str:
    clean_identifier = identifier.strip()
    if not CONTROL_IDENTIFIER_RE.fullmatch(clean_identifier):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Call identifier must be 1-128 characters using letters, numbers, "
                "dot, underscore, colon, or hyphen."
            ),
        )
    return clean_identifier


def _is_direct_plivo_hangup_identifier(identifier: str) -> bool:
    try:
        uuid.UUID(identifier)
    except ValueError:
        return False
    return True


def _is_valid_plivo_websocket_upgrade(websocket: WebSocket, settings: Settings) -> bool:
    if not settings.should_validate_plivo_signature:
        return True
    auth_token = settings.plivo_auth_token
    if auth_token is None:
        return False
    try:
        https_upgrade_url = _public_websocket_upgrade_url(websocket, settings)
    except UnicodeDecodeError:
        logger.warning("Rejected Plivo media WebSocket upgrade with non-ASCII query string.")
        return False
    # Plivo signs the wss:// Stream URL it actually connected to, but public_base_url is
    # https://. Validate against both schemes so a wss/https representation mismatch can't
    # reject a genuine Plivo media stream.
    wss_upgrade_url = https_upgrade_url.replace("https://", "wss://", 1)
    nonce = websocket.headers.get("X-Plivo-Signature-V3-Nonce")
    signature = websocket.headers.get("X-Plivo-Signature-V3")
    main_signature = websocket.headers.get("X-Plivo-Signature-Ma-V3")
    for candidate_url in (wss_upgrade_url, https_upgrade_url):
        if validate_websocket_v3_signature(
            url=candidate_url,
            nonce=nonce,
            auth_token=auth_token.get_secret_value(),
            signature_header=signature,
            main_account_signature_header=main_signature,
        ):
            return True
    logger.warning(
        "Plivo media WebSocket signature rejected (sig_present=%s nonce_present=%s url=%s).",
        bool(signature),
        bool(nonce),
        wss_upgrade_url,
    )
    return False


def _public_websocket_upgrade_url(websocket: WebSocket, settings: Settings) -> str:
    query_string = websocket.scope.get("query_string", b"")
    if isinstance(query_string, bytes):
        query_text = query_string.decode("ascii")
    else:
        query_text = str(query_string)
    public_url = settings.plivo_base_url + websocket.url.path
    if query_text:
        public_url = f"{public_url}?{query_text}"
    return public_url


def _replay_outbound_idempotency_result(
    *,
    existing_session: dict[str, Any],
    from_number: str,
    to_number: str,
) -> JSONResponse:
    if (
        existing_session.get("from_number") != from_number
        or existing_session.get("to_number") != to_number
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency-Key is already associated with a different outbound call.",
        )

    api_id = existing_session.get("api_id")
    message = existing_session.get("message")
    request_uuid = existing_session.get("request_uuid")
    if not all(isinstance(value, str) and value for value in (api_id, message, request_uuid)):
        if (
            existing_session.get("status") == "failed"
            and existing_session.get("event") == "outbound_create_failed"
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Outbound call creation previously failed for this Idempotency-Key. "
                    "Use a new Idempotency-Key to retry."
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Outbound call creation is already in progress for this Idempotency-Key.",
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "api_id": api_id,
            "message": message,
            "request_uuid": request_uuid,
        },
    )


async def _mark_outbound_create_failed(
    *,
    session_store: CallSessionStore,
    idempotency_key: str,
    from_number: str,
    to_number: str,
) -> None:
    await session_store.upsert(
        CallSessionUpdate(
            idempotency_key=idempotency_key,
            direction="outbound",
            from_number=from_number,
            to_number=to_number,
            status="failed",
            event="outbound_create_failed",
        )
    )


def _schedule_outbound_greeting_prewarm(
    *,
    settings: Settings,
    per_call_config: PerCallLLMConfig,
) -> asyncio.Task[None] | None:
    greeting = per_call_config.greeting
    if not isinstance(greeting, str) or not greeting.strip():
        return None

    async def prewarm() -> None:
        try:
            await prewarm_cached_tts_audio(
                settings=settings,
                text=greeting,
                purpose="greeting",
                tts_override=per_call_config.tts,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Outbound greeting audio prewarm failed.", exc_info=True)

    return asyncio.create_task(prewarm())


async def _best_effort_mark_outbound_create_failed(
    *,
    session_store: CallSessionStore,
    idempotency_key: str,
    from_number: str,
    to_number: str,
) -> None:
    try:
        await _mark_outbound_create_failed(
            session_store=session_store,
            idempotency_key=idempotency_key,
            from_number=from_number,
            to_number=to_number,
        )
    except Exception:
        logger.exception("Failed to mark outbound create failure in call-session storage.")


OutboundCallbackUrlResolver = Callable[[str], Awaitable[str | None]]


async def _outbound_callback_url_error(
    *,
    app: FastAPI,
    call_client: OutboundCallClient,
    answer_url: str,
) -> str | None:
    resolver = cast(
        OutboundCallbackUrlResolver | None,
        getattr(app.state, "outbound_callback_url_resolver", None),
    )
    if resolver is None and isinstance(call_client, PlivoOutboundCallClient):
        resolver = _resolve_public_callback_url
    if resolver is None:
        return None
    return await resolver(answer_url)


async def _resolve_public_callback_url(answer_url: str) -> str | None:
    parsed_url = urlsplit(answer_url)
    host = parsed_url.hostname
    if host is None:
        return "Public callback URL has no host."
    port = parsed_url.port or 443
    try:
        await asyncio.get_running_loop().getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
        )
    except OSError:
        return f"Public callback host {host} cannot be resolved."
    health_url = f"{parsed_url.scheme}://{parsed_url.netloc}/healthz"
    try:
        async with httpx.AsyncClient(
            timeout=OUTBOUND_CALLBACK_HEALTH_CHECK_TIMEOUT_SECONDS,
            follow_redirects=True,
        ) as client:
            response = await client.get(health_url)
    except httpx.RequestError:
        return f"Public callback health check {health_url} failed."
    if not response.is_success:
        return f"Public callback health check {health_url} returned HTTP {response.status_code}."
    return None


def _plivo_rejection_detail(exc: httpx.HTTPStatusError) -> str:
    reason = _plivo_response_reason(exc.response)
    if reason is None:
        return "Plivo rejected the outbound call request."
    return f"Plivo rejected the outbound call request: {reason}."


def _plivo_response_reason(response: httpx.Response) -> str | None:
    try:
        data = response.json()
    except ValueError:
        return _clean_provider_error_text(response.text)
    if not isinstance(data, dict):
        return None
    for key in ("error", "message", "detail"):
        value = data.get(key)
        if isinstance(value, str):
            return _clean_provider_error_text(value)
    return None


def _clean_provider_error_text(value: str) -> str | None:
    clean_value = " ".join(value.split())
    if not clean_value:
        return None
    return clean_value[:240]


async def _best_effort_hangup_after_untracked_outbound(
    *,
    call_client: OutboundCallClient,
    request_uuid: str,
) -> None:
    try:
        await call_client.hangup_call(request_uuid)
    except Exception:
        logger.exception("Failed to hang up Plivo call after call-session persistence failure.")


def _select_plivo_hangup_identifier(session: dict[str, Any]) -> str:
    for key in ("call_id", "request_uuid"):
        value = session.get(key)
        if isinstance(value, str) and value:
            return value
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Tracked call session does not include a Plivo call or request identifier.",
    )


async def _mark_control_hangup_requested(
    *,
    session_store: CallSessionStore,
    session: dict[str, Any],
    plivo_identifier: str,
) -> None:
    del plivo_identifier
    await session_store.upsert(
        CallSessionUpdate(
            idempotency_key=_session_string(session, "idempotency_key"),
            call_id=_session_string(session, "call_id"),
            request_uuid=_session_string(session, "request_uuid"),
            status="hangup_requested",
            event="control_hangup_requested",
        )
    )


async def _mark_control_hangup_failed(
    *,
    session_store: CallSessionStore,
    session: dict[str, Any],
) -> None:
    await session_store.upsert(
        CallSessionUpdate(
            idempotency_key=_session_string(session, "idempotency_key"),
            call_id=_session_string(session, "call_id"),
            request_uuid=_session_string(session, "request_uuid"),
            status="hangup_failed",
            event="control_hangup_failed",
        )
    )


async def _best_effort_mark_control_hangup_failed(
    *,
    session_store: CallSessionStore,
    session: dict[str, Any],
) -> None:
    try:
        await _mark_control_hangup_failed(session_store=session_store, session=session)
    except Exception:
        logger.exception("Failed to mark control hangup failure in call-session storage.")


def _session_string(session: dict[str, Any], key: str) -> str | None:
    value = session.get(key)
    return value if isinstance(value, str) and value else None


async def _record_answer_session(
    store: CallSessionStore,
    params: dict[str, Any],
) -> None:
    update = _session_update_from_callback(
        params,
        default_status="answered",
        default_event="answer",
    )
    if update is not None:
        await store.upsert(update)


async def _record_status_session(
    store: CallSessionStore,
    params: dict[str, Any],
) -> None:
    update = _session_update_from_callback(
        params,
        default_status="status_callback",
        default_event="status",
    )
    if update is not None:
        await store.upsert(update)


async def _record_callback_event(store: CallEventStore, params: dict[str, Any]) -> None:
    if params:
        await store.record(params)


def _build_voice_turn_latency_observer(
    store: CallEventStore,
    metrics: MetricsRegistry,
) -> Callable[[VoiceTurnLatencySnapshot], None]:
    def observe(snapshot: VoiceTurnLatencySnapshot) -> None:
        metrics.record_turn(snapshot.outcome)
        task = asyncio.create_task(_record_voice_turn_latency_event(store, snapshot))
        task.add_done_callback(_consume_voice_turn_latency_record_result)

    return observe


def _build_voice_action_observer(
    store: CallEventStore,
) -> Callable[[VoiceActionRequest], None]:
    def observe(request: VoiceActionRequest) -> None:
        task = asyncio.create_task(_record_voice_action_event(store, request))
        task.add_done_callback(_consume_voice_action_record_result)

    return observe


class _DebouncedTranscriptWriter:
    """Coalesces frequent full-transcript persists into at most one locked upsert per
    interval, with a leading write and a trailing flush so the latest transcript is always
    persisted. Avoids re-serializing the whole (growing) transcript on every single turn."""

    def __init__(
        self,
        store: CallSessionStore,
        *,
        call_id: str,
        interval_seconds: float,
    ) -> None:
        self._store = store
        self._call_id = call_id
        self._interval_seconds = interval_seconds
        self._latest: str | None = None
        self._pending_task: asyncio.Task[None] | None = None
        self._last_write_at: float | None = None

    def submit(self, transcript: str) -> None:
        self._latest = transcript
        if self._pending_task is not None and not self._pending_task.done():
            return  # a flush is already scheduled; it will pick up the latest value
        self._pending_task = asyncio.create_task(self._flush())
        self._pending_task.add_done_callback(_consume_call_transcript_record_result)

    async def _flush(self) -> None:
        loop = asyncio.get_running_loop()
        if self._last_write_at is not None:
            remaining = self._interval_seconds - (loop.time() - self._last_write_at)
            if remaining > 0:
                await asyncio.sleep(remaining)
        transcript = self._latest
        self._last_write_at = loop.time()
        if transcript is not None:
            await _record_call_transcript(self._store, self._call_id, transcript)


def _build_call_transcript_observer(
    store: CallSessionStore,
    *,
    call_id: str,
    interval_seconds: float = 0.0,
) -> Callable[[str], None]:
    if interval_seconds <= 0:

        def observe_immediately(transcript: str) -> None:
            task = asyncio.create_task(_record_call_transcript(store, call_id, transcript))
            task.add_done_callback(_consume_call_transcript_record_result)

        return observe_immediately

    writer = _DebouncedTranscriptWriter(store, call_id=call_id, interval_seconds=interval_seconds)
    return writer.submit


def _build_agent_hangup_observer(
    call_client: OutboundCallClient | None,
    store: CallSessionStore,
) -> Callable[[str], Awaitable[None]] | None:
    if call_client is None:
        return None

    async def observe(call_id: str) -> None:
        try:
            await call_client.hangup_call(call_id)
        except Exception:
            await _best_effort_mark_media_session(
                session_store=store,
                call_id=call_id,
                stream_id=None,
                status="hangup_failed",
                event="agent_closing_hangup_failed",
            )
            raise
        await store.upsert(
            CallSessionUpdate(
                call_id=call_id,
                status="hangup_requested",
                event="agent_closing_hangup_requested",
            )
        )

    return observe


async def _record_call_transcript(
    store: CallSessionStore,
    call_id: str,
    transcript: str,
) -> None:
    await store.upsert(CallSessionUpdate(call_id=call_id, transcript=transcript))


def _consume_call_transcript_record_result(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.warning("Failed to record call transcript.", exc_info=True)


async def _record_voice_turn_latency_event(
    store: CallEventStore,
    snapshot: VoiceTurnLatencySnapshot,
) -> None:
    await store.record(
        {
            "event": "voice_turn_latency",
            "outcome": snapshot.outcome,
            # Wall-clock stamp so per-call turns are orderable and joinable with session
            # events, independent of the in-stream perf-counter timings.
            "recorded_at_ms": int(time.time() * 1000),
            "call_id": snapshot.call_id,
            "stream_id": snapshot.stream_id,
            "turn_epoch": snapshot.turn_epoch,
            "transcript_chars": snapshot.transcript_chars,
            "turn_commit_source": snapshot.turn_commit_source,
            "response_plan_action": snapshot.response_plan_action,
            "response_plan_confidence": snapshot.response_plan_confidence,
            "response_plan_reason": snapshot.response_plan_reason,
            "media_start_to_first_audio_ms": snapshot.media_start_to_first_audio_ms,
            "first_audio_to_last_voice_frame_ms": (snapshot.first_audio_to_last_voice_frame_ms),
            "last_voice_frame_to_turn_committed_ms": (
                snapshot.last_voice_frame_to_turn_committed_ms
            ),
            "end_of_turn_silence_ms": snapshot.end_of_turn_silence_ms,
            "last_voice_frame_to_first_play_audio_ms": (
                snapshot.last_voice_frame_to_first_play_audio_ms
            ),
            "first_audio_to_speech_final_ms": snapshot.first_audio_to_speech_final_ms,
            "speech_final_to_turn_committed_ms": snapshot.speech_final_to_turn_committed_ms,
            "turn_committed_to_llm_first_chunk_ms": (snapshot.turn_committed_to_llm_first_chunk_ms),
            "turn_committed_to_first_reply_chunk_ms": (
                snapshot.turn_committed_to_first_reply_chunk_ms
            ),
            "llm_first_chunk_to_first_reply_chunk_ms": (
                snapshot.llm_first_chunk_to_first_reply_chunk_ms
            ),
            "llm_first_chunk_to_tts_first_audio_ms": (
                snapshot.llm_first_chunk_to_tts_first_audio_ms
            ),
            "first_reply_chunk_to_tts_first_audio_ms": (
                snapshot.first_reply_chunk_to_tts_first_audio_ms
            ),
            "tts_first_audio_to_first_play_audio_ms": (
                snapshot.tts_first_audio_to_first_play_audio_ms
            ),
            "turn_committed_to_first_play_audio_ms": (
                snapshot.turn_committed_to_first_play_audio_ms
            ),
            "first_audio_to_first_play_audio_ms": (snapshot.first_audio_to_first_play_audio_ms),
            "answer_first_play_audio_to_llm_stream_completed_ms": (
                snapshot.answer_first_play_audio_to_llm_stream_completed_ms
            ),
            "pre_response_ack_used": snapshot.pre_response_ack_used,
            "turn_committed_to_pre_response_ack_ms": (
                snapshot.turn_committed_to_pre_response_ack_ms
            ),
            "turn_committed_to_answer_first_play_audio_ms": (
                snapshot.turn_committed_to_answer_first_play_audio_ms
            ),
            "pre_response_ack_cache_hit": snapshot.pre_response_ack_cache_hit,
            "speculative_response_used": snapshot.speculative_response_used,
            "speculative_response_generation_started_to_commit_ms": (
                snapshot.speculative_response_generation_started_to_commit_ms
            ),
            "speculative_response_tts_first_audio_to_commit_ms": (
                snapshot.speculative_response_tts_first_audio_to_commit_ms
            ),
            "speculative_response_started_count": (snapshot.speculative_response_started_count),
            "speculative_response_discarded_count": (snapshot.speculative_response_discarded_count),
        }
    )


async def _record_voice_action_event(
    store: CallEventStore,
    request: VoiceActionRequest,
) -> None:
    await store.record(
        {
            "event": "voice_action_requested",
            "recorded_at_ms": int(time.time() * 1000),
            "call_id": request.call_id,
            "stream_id": request.stream_id,
            "turn_epoch": request.turn_epoch,
            "action_type": request.action_type,
            "action_payload": request.payload,
        }
    )


def _consume_voice_action_record_result(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.warning("Failed to record voice action event.", exc_info=True)


def _consume_voice_turn_latency_record_result(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.warning("Failed to record voice turn latency event.", exc_info=True)


def _session_update_from_callback(
    params: dict[str, Any],
    *,
    default_status: str,
    default_event: str,
) -> CallSessionUpdate | None:
    call_id = _first_callback_string(params, "CallUUID", "CallUuid", "CallSid", "call_uuid")
    request_uuid = _first_callback_string(params, "RequestUUID", "request_uuid", "requestUuid")
    if call_id is None and request_uuid is None:
        return None

    return CallSessionUpdate(
        call_id=call_id,
        request_uuid=request_uuid,
        direction=_first_callback_string(params, "Direction", "direction"),
        from_number=_first_callback_string(params, "From", "from", "FromNumber", "from_number"),
        to_number=_first_callback_string(params, "To", "to", "ToNumber", "to_number"),
        status=_first_callback_string(params, "CallStatus", "call_status") or default_status,
        event=_first_callback_string(params, "Event", "event") or default_event,
        last_event=params,
    )


def _validate_callback_identifiers(params: dict[str, Any]) -> None:
    for label, keys in (
        ("call", ("CallUUID", "CallUuid", "CallSid", "call_uuid")),
        ("request", ("RequestUUID", "request_uuid", "requestUuid")),
    ):
        value = _first_callback_identifier(params, label, keys)
        if value is None:
            continue
        if CONTROL_IDENTIFIER_RE.fullmatch(value) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Plivo callback {label} identifier must be 1-128 characters using "
                    "letters, numbers, dot, underscore, colon, or hyphen."
                ),
            )


def _first_callback_identifier(
    params: dict[str, Any],
    label: str,
    keys: tuple[str, ...],
) -> str | None:
    selected_identifier: str | None = None
    for key in keys:
        value = params.get(key)
        if not isinstance(value, str):
            continue
        clean_value = value.strip()
        if not clean_value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Plivo callback {label} identifier must not be blank.",
            )
        if selected_identifier is None:
            selected_identifier = clean_value
            continue
        if selected_identifier != clean_value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Plivo callback {label} identifier aliases conflict.",
            )
    return selected_identifier


def _validate_callback_semantic_aliases(params: dict[str, Any]) -> None:
    for label, keys in CALLBACK_SEMANTIC_ALIAS_GROUPS:
        _first_callback_alias_value(params, label, keys)


def _validate_callback_lifecycle_values(params: dict[str, Any]) -> None:
    for label, keys in CALLBACK_LIFECYCLE_VALUE_GROUPS:
        value = _first_callback_alias_value(params, label, keys)
        if value is None:
            continue
        if CALLBACK_LIFECYCLE_VALUE_RE.fullmatch(value) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Plivo callback {label} value must be 1-64 characters using "
                    "letters, numbers, dot, underscore, colon, or hyphen."
                ),
            )


def _validate_callback_session_metadata_values(params: dict[str, Any]) -> None:
    for label, keys in CALLBACK_DIRECTION_VALUE_GROUPS:
        value = _first_callback_alias_value(params, label, keys)
        if value is None:
            continue
        if CALLBACK_LIFECYCLE_VALUE_RE.fullmatch(value) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Plivo callback {label} value must be 1-64 characters using "
                    "letters, numbers, dot, underscore, colon, or hyphen."
                ),
            )
    for label, keys in CALLBACK_ENDPOINT_VALUE_GROUPS:
        value = _first_callback_alias_value(params, label, keys)
        if value is None:
            continue
        if CALLBACK_ENDPOINT_VALUE_RE.fullmatch(value) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Plivo callback {label} value must be 1-128 characters using "
                    "safe endpoint characters."
                ),
            )


def _first_callback_alias_value(
    params: dict[str, Any],
    label: str,
    keys: tuple[str, ...],
) -> str | None:
    selected_value: str | None = None
    for key in keys:
        value = params.get(key)
        if not isinstance(value, str):
            continue
        clean_value = value.strip()
        if not clean_value:
            continue
        if selected_value is None:
            selected_value = clean_value
            continue
        if selected_value != clean_value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Plivo callback {label} aliases conflict.",
            )
    return selected_value


def _provider_status(settings: Settings) -> dict[str, str]:
    return {
        "stt": settings.stt_provider,
        "llm": settings.llm_provider,
        "tts": settings.tts_provider,
    }


def _answer_url_with_cfg(answer_url: str, cfg_id: str) -> str:
    separator = "&" if "?" in answer_url else "?"
    return f"{answer_url}{separator}{urlencode({'cfg': cfg_id})}"


def _outbound_time_limit_seconds(
    settings: Settings,
    behavior: dict[str, Any] | None,
) -> int:
    value = (behavior or {}).get("max_call_duration_seconds")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return settings.outbound_time_limit_seconds
    parsed = int(value)
    if not 30 <= parsed <= 7200:
        return settings.outbound_time_limit_seconds
    return parsed


async def _resolve_inbound_cfg(
    app: FastAPI,
    params: dict[str, Any],
) -> tuple[str | None, str | None]:
    """For an inbound call, resolve the dialed number's per-call LLM overrides from
    the control plane and stash them under a fresh cfg id (which then rides the media
    WS URL). Returns (cfg_id, disabled_message). When there is no control-plane client,
    the call is not inbound, or nothing is configured, both values are None and the call
    runs on the agent's env defaults."""
    client = app.state.control_plane_client
    if client is None:
        return None, None
    direction = _first_callback_string(params, "Direction", "direction")
    if direction != "inbound":
        return None, None
    number = _first_callback_string(params, "To", "to", "ToNumber", "to_number")
    if number is None:
        return None, None
    config = await client.fetch_inbound_config(number)
    if config is None:
        return None, None
    behavior = config.behavior or {}
    if behavior.get("inbound_enabled") is False:
        message = behavior.get("closing_message")
        return None, message if isinstance(message, str) and message.strip() else ""
    cfg_id = token_urlsafe(18)
    await app.state.per_call_config_store.put(cfg_id, config)
    return cfg_id, None


def _build_answer_media_websocket_url(
    settings: Settings, params: dict[str, Any], cfg_id: str | None = None
) -> str:
    websocket_url = settings.media_websocket_url
    query: dict[str, str] = {}
    if settings.stream_auth_token is not None:
        call_id = _first_callback_string(params, "CallUUID", "CallUuid", "CallSid", "call_uuid")
        if call_id is None:
            raise HTTPException(
                status_code=400,
                detail="Plivo answer callback is missing a call identifier.",
            )
        query["token"] = build_stream_token(
            signing_secret=settings.stream_auth_token.get_secret_value(),
            ttl_seconds=settings.stream_token_ttl_seconds,
            call_id=call_id,
            cfg=cfg_id or "",
        )
    # Per-call LLM config id rides alongside the (optional) stream token so the
    # media handler can resolve a project's model/prompt overrides for this call.
    if cfg_id:
        query["cfg"] = cfg_id
    if not query:
        return websocket_url
    separator = "&" if "?" in websocket_url else "?"
    return f"{websocket_url}{separator}{urlencode(query)}"


def _verify_stream_token_at_connect(
    websocket: WebSocket,
    settings: Settings,
) -> StreamTokenClaims | None:
    if settings.stream_auth_token is None:
        return None
    supplied_token = websocket.query_params.get("token")
    if supplied_token is None:
        return None
    return verify_stream_token(
        token=supplied_token,
        signing_secret=settings.stream_auth_token.get_secret_value(),
        cfg=websocket.query_params.get("cfg", ""),
    )


def _is_allowed_stream_claims(
    claims: StreamTokenClaims | None,
    settings: Settings,
) -> bool:
    if settings.stream_auth_token is None:
        return True
    return claims is not None


def _is_valid_start_for_stream_claims(
    claims: StreamTokenClaims | None,
    settings: Settings,
    call_id: str,
) -> bool:
    if settings.stream_auth_token is None:
        return True
    return claims is not None and claims.call_id == call_id


def _first_callback_string(source: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _build_outbound_call_client(settings: Settings) -> OutboundCallClient | None:
    if not settings.plivo_auth_id or not settings.plivo_auth_token:
        return None
    return PlivoOutboundCallClient(
        auth_id=settings.plivo_auth_id,
        auth_token=settings.plivo_auth_token.get_secret_value(),
        api_base_url=settings.plivo_api_base_url,
        timeout_seconds=settings.plivo_api_timeout_seconds,
        max_response_bytes=settings.plivo_api_max_response_bytes,
    )
