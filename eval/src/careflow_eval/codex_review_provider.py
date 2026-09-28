"""Isolated, attributable subscription Codex calls for offline synthetic-data review (M2-03).

Claude wrote the Arabic scenarios, so a Claude reviewer is not independent of them; Codex (a GPT
model) is `eval_judge`'s configured tool. This adapter keeps the Claude adapter's boundary:
offline only, a filtered environment, an empty temporary working directory, a read-only sandbox,
the user's Codex config and rules ignored, the model named explicitly, and a bounded budget. The
reviewer's identity comes from Codex's own session log for the call (`turn_context.model`), not
from the request alone. Only the model, provider, CLI version and sandbox are read from that log;
its account and user ids are never read out.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
import tempfile
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from careflow_eval.cli_review_provider import _child_environment, _digest, _stop_process_group
from careflow_providers.adapters.cli import render_prompt
from careflow_providers.base import ChatResult, Message
from careflow_providers.errors import LivePathForbidden, ProviderUnavailable

EFFORTS = ("low", "medium", "high")


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def codex_identity(thread_id: str, *, home: Path | None = None) -> dict[str, Any]:
    """What Codex recorded for one thread in its session log: the session's provider, CLI
    version, originator and source, and each turn's model, effort, sandbox and approval policy."""
    if not re.fullmatch(r"[0-9a-f-]{36}", thread_id):
        raise ValueError("Codex thread id is not a UUID")
    found = sorted((home or codex_home()).glob(f"sessions/*/*/*/rollout-*-{thread_id}.jsonl"))
    if len(found) != 1:
        raise ValueError("Codex session log for the review thread was not found exactly once")
    session: dict[str, Any] | None = None
    turns: list[dict[str, Any]] = []
    for line in found[0].read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        payload = event.get("payload") if isinstance(event, dict) else None
        if not isinstance(payload, dict):
            continue
        if event.get("type") == "session_meta" and session is None:
            keys = ("id", "model_provider", "cli_version", "originator", "source")
            session = {key: payload.get(key) for key in keys}
        elif event.get("type") == "turn_context":
            sandbox = payload.get("sandbox_policy")
            turns.append(
                {
                    "model": payload.get("model"),
                    "effort": payload.get("effort"),
                    "sandbox": sandbox.get("type") if isinstance(sandbox, dict) else None,
                    "approval_policy": payload.get("approval_policy"),
                }
            )
    if session is None or session.get("id") != thread_id or not turns:
        raise ValueError("Codex session log is incomplete")
    return {"session": session, "turns": turns}


def verify_identity(identity: Mapping[str, Any], requested: str, effort: str) -> None:
    session, turns = identity["session"], identity["turns"]
    if session.get("model_provider") != "openai" or session.get("source") != "exec":
        raise ValueError("Codex review did not run as a first-party exec session")
    for turn in turns:
        if turn != {
            "model": requested,
            "effort": effort,
            "sandbox": "read-only",
            "approval_policy": "never",
        }:
            raise ValueError("Codex review turn did not use the requested model and sandbox")


def parse_events(stdout: bytes) -> dict[str, Any]:
    """The thread id, the final usage and any failure from `codex exec --json` events."""
    events = []
    for line in stdout.decode("utf-8").splitlines():
        if line.strip():
            try:
                events.append(json.loads(line))
            except ValueError as exc:
                raise ValueError("Codex review printed a non-JSON event") from exc
    threads = [event.get("thread_id") for event in events if event.get("type") == "thread.started"]
    completed = [event for event in events if event.get("type") == "turn.completed"]
    failed = [event for event in events if event.get("type") in {"turn.failed", "error"}]
    if len(threads) != 1 or not isinstance(threads[0], str):
        raise ValueError("Codex review did not start exactly one thread")
    if failed or len(completed) != 1:
        raise ValueError("Codex review turn did not complete")
    return {"thread_id": threads[0], "usage": completed[0].get("usage"), "events": events}


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _last_message(path: Path) -> Any:
    """The final message Codex wrote, parsed as JSON; None when there is none."""
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


class AuditedCodexReviewProvider:
    """ChatProvider for an explicitly selected Codex model and a bounded offline review budget."""

    def __init__(
        self,
        *,
        model_id: str,
        schema: Mapping[str, Any],
        task: str = "eval_judge",
        tool: str = "codex",
        timeout_seconds: float = 300.0,
        max_calls: int = 199,
        effort: str = "high",
        home: Path | None = None,
    ) -> None:
        if tool != "codex" or task != "eval_judge":
            raise ValueError("Audited Codex review requires the configured codex eval_judge")
        if not re.fullmatch(r"gpt-[A-Za-z0-9][A-Za-z0-9.-]*", model_id):
            raise ValueError("Review requires an explicit Codex model ID, not an alias")
        if effort not in EFFORTS:
            raise ValueError(f"Codex review effort must be one of {EFFORTS}")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("CLI review timeout must be finite and positive")
        if (
            isinstance(max_calls, bool)
            or not isinstance(max_calls, int)
            or not 1 <= max_calls <= 199
        ):
            raise ValueError("CLI review budget must be 1..199, reserving the issue's first probe")
        self.task, self.tool, self.model_name = task, tool, model_id
        self.verified_model_id: str | None = None
        self.raw_cli_evidence: list[dict[str, Any]] = []
        self.auth_receipt: dict[str, Any] | None = None
        self.calls_made = 0
        self._max_calls, self._timeout, self._effort = max_calls, timeout_seconds, effort
        self._home = home
        self._schema = json.dumps(dict(schema), sort_keys=True, separators=(",", ":"))
        self._lock = asyncio.Lock()

    @property
    def remaining_calls(self) -> int:
        return self._max_calls - self.calls_made

    def _offline(self) -> None:
        if os.environ.get("CAREFLOW_RUNTIME") != "offline":
            raise LivePathForbidden("Codex review requires CAREFLOW_RUNTIME=offline")

    def _executable(self) -> str:
        executable = shutil.which("codex", path=_child_environment().get("PATH"))
        if executable is None:
            raise ProviderUnavailable(self.task, "codex is not installed")
        return executable

    async def _run(
        self,
        argv: Sequence[str],
        *,
        cwd: str,
        input_bytes: bytes | None,
        timeout_seconds: float,
        evidence: dict[str, Any] | None = None,
    ) -> tuple[int, bytes, bytes]:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=_child_environment(),
            start_new_session=True,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=input_bytes), timeout=timeout_seconds
            )
        except (TimeoutError, asyncio.CancelledError) as exc:
            if evidence is not None:
                evidence["failure"] = type(exc).__name__
            await _stop_process_group(process)
            if evidence is not None:
                evidence["exit_code"] = process.returncode
            raise
        return process.returncode if process.returncode is not None else -1, stdout, stderr

    async def verify_auth(self) -> dict[str, Any]:
        """A ChatGPT subscription login, never an API key (zero cost)."""
        self._offline()
        with tempfile.TemporaryDirectory(prefix="careflow-review-codex-") as cwd:
            exit_code, stdout, stderr = await self._run(
                [self._executable(), "login", "status"],
                cwd=cwd,
                input_bytes=None,
                timeout_seconds=min(self._timeout, 15.0),
            )
        status = (stdout + stderr).decode("utf-8", "replace")
        if exit_code != 0 or "Logged in using ChatGPT" not in status:
            raise ProviderUnavailable(self.task, "Codex ChatGPT subscription login not verified")
        self.auth_receipt = {
            "loggedIn": True,
            "authMethod": "chatgpt",
            "checked_at": datetime.now(UTC).isoformat(),
        }
        return dict(self.auth_receipt)

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        del temperature, max_tokens  # The CLI controls sampling; the receipt records its result.
        async with self._lock:
            self._offline()
            self.verified_model_id = None
            if self.remaining_calls <= 0:
                raise ProviderUnavailable(self.task, "Codex review invocation budget exhausted")
            auth = await self.verify_auth()
            prompt = render_prompt(messages).encode("utf-8")
            with tempfile.TemporaryDirectory(prefix="careflow-review-codex-") as work:
                schema_path = Path(work, "schema.json")
                _write(schema_path, self._schema)
                last_message = Path(work, "last-message.txt")
                argv = [
                    self._executable(),
                    "exec",
                    "--json",
                    "--skip-git-repo-check",
                    "--ignore-user-config",
                    "--ignore-rules",
                    "--sandbox",
                    "read-only",
                    "--model",
                    self.model_name,
                    "--config",
                    f'model_reasoning_effort="{self._effort}"',
                    "--output-schema",
                    str(schema_path),
                    "--output-last-message",
                    str(last_message),
                    "--cd",
                    work,
                    "-",
                ]
                evidence: dict[str, Any] = {
                    "started_at": datetime.now(UTC).isoformat(),
                    "argv": [*argv[:1], *(arg.replace(work, "<tmp>") for arg in argv[1:])],
                    "prompt_sha256": _digest(prompt),
                    "requested_model_id": self.model_name,
                    "effort": self._effort,
                    "auth": auth,
                    "exit_code": None,
                    "result": None,
                }
                self.calls_made += 1
                evidence["call_number"] = self.calls_made
                self.raw_cli_evidence.append(evidence)
                try:
                    exit_code, stdout, stderr = await self._run(
                        argv,
                        cwd=work,
                        input_bytes=prompt,
                        timeout_seconds=self._timeout,
                        evidence=evidence,
                    )
                    evidence.update(
                        exit_code=exit_code,
                        stdout_sha256=_digest(stdout),
                        stderr_sha256=_digest(stderr),
                    )
                    try:
                        run = parse_events(stdout)
                    except (ValueError, UnicodeDecodeError) as exc:
                        raise ProviderUnavailable(self.task, str(exc)) from exc
                    evidence.update(thread_id=run["thread_id"], usage=run["usage"])
                    evidence["result"] = run["events"]
                    try:
                        output = _last_message(last_message)
                        identity = codex_identity(run["thread_id"], home=self._home)
                        verify_identity(identity, self.model_name, self._effort)
                    except ValueError as exc:
                        raise ProviderUnavailable(self.task, str(exc)) from exc
                    if exit_code != 0 or output is None:
                        raise ProviderUnavailable(self.task, "Codex review did not finish")
                    evidence["identity"] = identity
                    if not isinstance(output, dict):
                        raise ProviderUnavailable(self.task, "Codex review has no JSON object")
                    self.verified_model_id = self.model_name
                    evidence["verified_model_id"] = self.verified_model_id
                    return ChatResult(
                        text=json.dumps(output, ensure_ascii=False), model=self.model_name
                    )
                except BaseException as exc:
                    evidence.setdefault("failure", type(exc).__name__)
                    raise

    async def stream(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        yield (await self.chat(messages)).text


__all__ = ["AuditedCodexReviewProvider", "codex_identity", "parse_events", "verify_identity"]
