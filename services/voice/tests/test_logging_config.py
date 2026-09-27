import json
import logging
import sys
from collections.abc import Iterator

import pytest

from voice_agent.logging_config import (
    JsonLogFormatter,
    configure_logging,
    resolve_log_level,
)


@pytest.fixture
def restore_package_logger() -> Iterator[None]:
    logger = logging.getLogger("voice_agent")
    saved_handlers = logger.handlers[:]
    saved_level = logger.level
    saved_propagate = logger.propagate
    try:
        yield
    finally:
        logger.handlers = saved_handlers
        logger.setLevel(saved_level)
        logger.propagate = saved_propagate


def test_json_formatter_emits_structured_fields_with_context() -> None:
    record = logging.makeLogRecord(
        {
            "name": "voice_agent.media.session",
            "levelname": "INFO",
            "levelno": logging.INFO,
            "msg": "turn committed",
            "call_id": "call-1",
            "stream_id": "stream-1",
            "turn_epoch": 3,
        }
    )

    payload = json.loads(JsonLogFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "voice_agent.media.session"
    assert payload["message"] == "turn committed"
    assert payload["call_id"] == "call-1"
    assert payload["stream_id"] == "stream-1"
    assert payload["turn_epoch"] == 3
    assert "ts" in payload


def test_json_formatter_omits_absent_context_fields() -> None:
    record = logging.makeLogRecord(
        {"name": "voice_agent", "levelname": "WARNING", "msg": "plain message"}
    )

    payload = json.loads(JsonLogFormatter().format(record))

    assert payload["message"] == "plain message"
    assert "call_id" not in payload
    assert "request_id" not in payload


def test_json_formatter_includes_exception_text() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord(
            name="voice_agent",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failed",
            args=(),
            exc_info=sys.exc_info(),
        )

    payload = json.loads(JsonLogFormatter().format(record))

    assert "ValueError: boom" in payload["exc_info"]


def test_resolve_log_level_maps_names_and_defaults() -> None:
    assert resolve_log_level("debug") == logging.DEBUG
    assert resolve_log_level("INFO") == logging.INFO
    assert resolve_log_level(" warning ") == logging.WARNING
    assert resolve_log_level("nonsense") == logging.INFO


def test_configure_logging_scopes_to_package_logger(restore_package_logger: None) -> None:
    del restore_package_logger
    configure_logging(level=logging.DEBUG, json_format=True)

    logger = logging.getLogger("voice_agent")
    assert logger.level == logging.DEBUG
    assert logger.propagate is False
    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0].formatter, JsonLogFormatter)
    # The root logger must be left untouched so uvicorn's own logging is undisturbed.
    assert logging.getLogger().handlers != logger.handlers
