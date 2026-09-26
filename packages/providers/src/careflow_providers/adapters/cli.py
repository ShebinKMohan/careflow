"""Subscription CLIs (Claude Code, Codex) for offline jobs only (ADR-004).

Runs the CLI with an argument list (never a shell) and a 300 s timeout. Refuses to run unless
`CAREFLOW_RUNTIME=offline`; the API service sets `CAREFLOW_RUNTIME=live`.

- Claude Code: `claude -p <prompt> --output-format json`, reply in the `result` field.
- Codex (0.154): `codex exec --json -o <file> <prompt>`; `--output-last-message` writes the final
  agent message to the file, so no JSONL event names are relied on (JSONL is the fallback).
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Final

from careflow_providers.base import ChatResult, Message
from careflow_providers.errors import LivePathForbidden, ProviderUnavailable

TIMEOUT_SECONDS: Final = 300.0


def render_prompt(messages: Sequence[Message]) -> str:
    """CLIs take one prompt: fold the conversation into labelled sections."""
    parts = [f"[{m['role']}]\n{m['content']}" for m in messages]
    return "\n\n".join(parts)


def parse_claude(stdout: str) -> str:
    """`claude -p ... --output-format json` prints one JSON object with a `result` field."""
    return str(json.loads(stdout)["result"])


def parse_codex(stdout: str) -> str:
    """`codex exec --json` prints JSONL events; the reply is the last agent message."""
    reply = ""
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        event = json.loads(line)
        item = event.get("item") or event.get("msg") or {}
        kind = item.get("type") or item.get("item_type") or event.get("type")
        if kind in {"agent_message", "assistant_message"}:
            reply = str(item.get("text") or item.get("message") or reply)
    if not reply:
        raise ValueError("codex exec produced no agent message")
    return reply


class CliProvider:
    def __init__(self, task: str, tool: str, *, timeout_seconds: float = TIMEOUT_SECONDS) -> None:
        self.task = task
        self.tool = tool
        self.model_name = tool
        self._timeout = timeout_seconds

    def _argv(self, prompt: str, last_message: Path) -> list[str]:
        if self.tool == "claude":
            return ["claude", "-p", prompt, "--output-format", "json"]
        return ["codex", "exec", "--json", "-o", str(last_message), prompt]

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        if os.environ.get("CAREFLOW_RUNTIME") != "offline":
            raise LivePathForbidden(
                f"subscription CLI {self.tool!r} (task {self.task!r}) needs "
                "CAREFLOW_RUNTIME=offline"
            )
        with tempfile.TemporaryDirectory(prefix="careflow-cli-") as tmp:
            last_message = Path(tmp) / "last-message.txt"
            argv = self._argv(render_prompt(messages), last_message)
            try:
                process = await asyncio.create_subprocess_exec(
                    *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
            except FileNotFoundError as exc:
                raise ProviderUnavailable(self.task, f"{self.tool} is not installed") from exc
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), self._timeout)
            except TimeoutError:
                process.kill()
                await process.wait()
                raise
            if process.returncode != 0:
                detail = stderr.decode()[:300]
                raise RuntimeError(f"{self.tool} exited {process.returncode}: {detail}")
            text = stdout.decode()
            if self.tool == "claude":
                reply = parse_claude(text)
            elif last_message.exists() and last_message.read_text(encoding="utf-8").strip():
                reply = last_message.read_text(encoding="utf-8").strip()
            else:
                reply = parse_codex(text)
        return ChatResult(text=reply, model=self.tool)

    async def stream(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        """CLIs do not stream: the whole reply arrives as one chunk."""
        yield (await self.chat(messages)).text
