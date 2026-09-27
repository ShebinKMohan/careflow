"""Structured logging setup for the voice agent.

Without explicit configuration the package's ``logger.info(...)`` lines never appear
(uvicorn leaves the root logger at WARNING), so latency/lifecycle logs are invisible in
production. ``configure_logging`` attaches a single JSON handler to the ``voice_agent``
logger only — it does not touch the root or uvicorn loggers — so it is safe to call from
the process entrypoint and never interferes with pytest's ``caplog``.

Call-scoped context (call_id, stream_id, turn_epoch, request_id) is emitted whenever it is
passed via ``logger.info(..., extra={"call_id": ...})``, so a single bad live call can be
traced by filtering on its call_id.
"""

from __future__ import annotations

import logging

import orjson

_CONTEXT_FIELDS = ("call_id", "stream_id", "turn_epoch", "request_id")
_LEVEL_BY_NAME: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


class JsonLogFormatter(logging.Formatter):
    """Render log records as one compact JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return orjson.dumps(payload).decode("utf-8")


def resolve_log_level(level: str) -> int:
    """Map a configured level name to a logging constant, defaulting to INFO."""
    return _LEVEL_BY_NAME.get(level.strip().upper(), logging.INFO)


def configure_logging(*, level: int = logging.INFO, json_format: bool = True) -> None:
    """Configure the ``voice_agent`` logger with a single (JSON or text) handler.

    Scoped to the package logger and ``propagate=False`` so it neither duplicates lines via
    the root logger nor disturbs uvicorn's own access/error logging.
    """
    handler: logging.Handler = logging.StreamHandler()
    if json_format:
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    package_logger = logging.getLogger("voice_agent")
    package_logger.handlers = [handler]
    package_logger.setLevel(level)
    package_logger.propagate = False
