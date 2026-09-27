from __future__ import annotations

import uvicorn
from fastapi import FastAPI

from voice_agent.careflow_app import create_careflow_app
from voice_agent.config.settings import get_settings
from voice_agent.logging_config import configure_logging, resolve_log_level


def build_app() -> FastAPI:
    return create_careflow_app(preload=True)


def run() -> None:
    settings = get_settings()
    configure_logging(
        level=resolve_log_level(settings.log_level),
        json_format=settings.log_format == "json",
    )
    # A single worker per process is intentional: session state lives in-process, so scale
    # horizontally rather than with --workers. timeout_graceful_shutdown lets uvicorn keep
    # serving in-flight WebSockets on SIGTERM until they finish or the grace window elapses.
    uvicorn.run(
        "voice_agent.main:build_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=False,
        ws="auto",
        timeout_graceful_shutdown=int(settings.shutdown_grace_seconds),
    )


if __name__ == "__main__":
    run()
