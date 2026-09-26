"""The subscription-CLI adapter: offline guard, and a fake `claude` on PATH."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from careflow_providers import LivePathForbidden, ProviderUnavailable
from careflow_providers.adapters.cli import CliProvider, parse_codex, render_prompt

MESSAGES = [{"role": "user", "content": "Write one synthetic triage scenario."}]


def fake_tool(directory: Path, name: str, stdout: str) -> None:
    script = directory / name
    script.write_text(f"#!/bin/sh\ncat <<'JSON'\n{stdout}\nJSON\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)


@pytest.mark.parametrize("runtime", [None, "live", "OFFLINE"])
async def test_refuses_outside_offline_jobs(
    runtime: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    if runtime is None:
        monkeypatch.delenv("CAREFLOW_RUNTIME", raising=False)
    else:
        monkeypatch.setenv("CAREFLOW_RUNTIME", runtime)
    with pytest.raises(LivePathForbidden):
        await CliProvider("test_data", "claude").chat(MESSAGES)  # type: ignore[arg-type]


async def test_fake_claude_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_tool(tmp_path, "claude", json.dumps({"type": "result", "result": "A synthetic scenario."}))
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CAREFLOW_RUNTIME", "offline")
    result = await CliProvider("test_data", "claude").chat(MESSAGES)  # type: ignore[arg-type]
    assert result.text == "A synthetic scenario."
    assert result.model == "claude"


async def test_missing_cli_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))  # no claude here
    monkeypatch.setenv("CAREFLOW_RUNTIME", "offline")
    with pytest.raises(ProviderUnavailable):
        await CliProvider("test_data", "claude").chat(MESSAGES)  # type: ignore[arg-type]


def test_codex_jsonl_fallback_takes_the_last_agent_message() -> None:
    stdout = "\n".join(
        json.dumps(e)
        for e in [
            {"type": "thread.started"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "draft"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "final"}},
            {"type": "turn.completed"},
        ]
    )
    assert parse_codex(stdout) == "final"


def test_prompt_rendering_keeps_roles() -> None:
    prompt = render_prompt([{"role": "system", "content": "S"}, {"role": "user", "content": "U"}])
    assert prompt == "[system]\nS\n\n[user]\nU"
