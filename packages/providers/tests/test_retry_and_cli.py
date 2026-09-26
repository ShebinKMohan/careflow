"""Ported retry helpers and the `careflow providers show` command."""

from __future__ import annotations

import httpx
from typer.testing import CliRunner

from careflow_providers._retry import RETRYABLE_STATUSES, compute_backoff, retry_after_seconds
from careflow_providers.cli import app


def test_backoff_is_capped_exponential() -> None:
    assert [compute_backoff(a, base_seconds=0.5, max_seconds=3) for a in (1, 2, 3, 4)] == [
        0.5,
        1.0,
        2.0,
        3,
    ]


def test_retry_after_parsing() -> None:
    def response(value: str | None) -> httpx.Response:
        headers = {} if value is None else {"retry-after": value}
        return httpx.Response(429, headers=headers)

    assert retry_after_seconds(response("2"), max_seconds=10) == 2.0
    assert retry_after_seconds(response("60"), max_seconds=10) == 10
    assert retry_after_seconds(response("Wed, 21 Oct 2026 07:28:00 GMT"), max_seconds=10) is None
    assert retry_after_seconds(response("-1"), max_seconds=10) is None
    assert retry_after_seconds(response(None), max_seconds=10) is None
    assert {429, 500, 502, 503, 504} == RETRYABLE_STATUSES


def test_providers_show_lists_every_task() -> None:
    result = CliRunner().invoke(app, ["providers", "show"])
    assert result.exit_code == 0, result.output
    for task in (
        "conversation",
        "extraction",
        "summary",
        "eval_judge",
        "test_data",
        "translation_check",
    ):
        assert task in result.output
    assert "no (offline only)" in result.output
