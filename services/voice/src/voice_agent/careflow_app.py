"""The CareFlow voice service (port 8090): the agent's app plus the kiosk channel.

- The agent's phone channel (`/plivo/*`, and the `/calls/*` and `/preview/*` control routes
  that drive it) is removed unless `VOICE_PLIVO_ENABLED=true` (default false).
- `WS /kiosk/audio` is added (voice_agent.kiosk).
- `GET /latency/summary` (the agent's route) now also reports kiosk turn p50/p95.
- With `preload`, the speech engines load at startup so the first patient does not wait.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from fastapi import FastAPI
from starlette.routing import BaseRoute

from voice_agent.app import create_app
from voice_agent.config.careflow import LANGUAGE
from voice_agent.config.settings import Settings, get_settings
from voice_agent.kiosk.speech import KioskSpeech, LocalKioskSpeech
from voice_agent.kiosk.turns import TurnPoster, TurnsClient
from voice_agent.kiosk.ws import KioskChannel
from voice_agent.kiosk.ws import router as kiosk_router
from voice_agent.providers.base import StartableProvider
from voice_agent.storage.call_events import CallEventStore

logger = logging.getLogger(__name__)

PHONE_ROUTE_PREFIXES: Final = ("/plivo", "/calls", "/preview")


def _is_phone_route(route: BaseRoute) -> bool:
    path = getattr(route, "path", "")
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in PHONE_ROUTE_PREFIXES)


def create_careflow_app(
    settings: Settings | None = None,
    *,
    speech: KioskSpeech | None = None,
    turns: TurnPoster | None = None,
    preload: bool = False,
) -> FastAPI:
    resolved = settings or get_settings()
    app = create_app(resolved)
    app.title = "CareFlow voice service"
    if not resolved.plivo_enabled:
        app.router.routes[:] = [route for route in app.router.routes if not _is_phone_route(route)]
    app.include_router(kiosk_router)

    events: CallEventStore = app.state.call_event_store
    kiosk_speech = speech or LocalKioskSpeech(resolved)
    kiosk_turns = turns or TurnsClient(
        base_url=resolved.careflow_api_base_url,
        timeout_seconds=resolved.kiosk_api_timeout_seconds,
    )
    app.state.kiosk = KioskChannel(
        settings=resolved, speech=kiosk_speech, turns=kiosk_turns, events=events
    )

    agent_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if preload:
            await _preload(kiosk_speech)
        async with agent_lifespan(app):
            yield
        if isinstance(kiosk_turns, TurnsClient):
            await kiosk_turns.aclose()

    app.router.lifespan_context = lifespan
    return app


async def _preload(speech: KioskSpeech) -> None:
    """Warm default engines once and release the per-call parts.

    The default-language STT (mlx-whisper serves Arabic with the same model) and TTS are warmed.
    Arabic synthesis loads on its first Arabic session, preserving Qwen's memory headroom on
    the 18 GB development machine. Arabic engines remain configured and available on demand.
    Whisper releases its weights after warm-up and after each real transcription.
    """
    started = asyncio.get_running_loop().time()
    providers = [speech.stt(), speech.tts(LANGUAGE)]
    try:
        for provider in providers:
            if isinstance(provider, StartableProvider):
                try:
                    await provider.start()
                except Exception:
                    logger.exception(
                        "Preloading %s failed; the kiosk channel will retry.",
                        type(provider).__name__,
                    )
        speech.vad().reset()
    finally:
        for provider in providers:
            await provider.close()
    logger.info(
        "Default-language speech warm-up finished in %.1f s; Arabic synthesis loads on first use.",
        asyncio.get_running_loop().time() - started,
    )
