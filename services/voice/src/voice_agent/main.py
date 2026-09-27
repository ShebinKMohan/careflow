from __future__ import annotations

import uvicorn

from voice_agent.app import create_app
from voice_agent.config.settings import get_settings
from voice_agent.logging_config import configure_logging, resolve_log_level

app = create_app()


def run() -> None:
    settings = get_settings()
    configure_logging(
        level=resolve_log_level(settings.log_level),
        json_format=settings.log_format == "json",
    )
    # A single worker per process is intentional: media-session state lives in-process, so
    # scale horizontally (more containers) rather than with --workers. timeout_graceful_shutdown
    # lets uvicorn keep serving in-flight media WebSockets on SIGTERM (rolling deploy, scale-in)
    # until they finish or the grace window elapses, instead of cancelling live calls.
    uvicorn.run(
        "voice_agent.main:app",
        host="0.0.0.0",
        port=8080,
        reload=False,
        timeout_graceful_shutdown=int(settings.shutdown_grace_seconds),
    )


if __name__ == "__main__":
    run()

