"""Isolated, attributable subscription Claude calls for offline synthetic-data review."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import signal
import tempfile
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from careflow_providers.adapters.cli import render_prompt
from careflow_providers.base import ChatResult, Message
from careflow_providers.errors import LivePathForbidden, ProviderUnavailable


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _child_environment() -> dict[str, str]:
    """Keep subscription/keychain access, never inherited API/provider or nested-session routing."""
    allowed = {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TMPDIR",
        "TEMP",
        "TMP",
        "LANG",
        "TERM",
        "COLORTERM",
        "NO_COLOR",
        "CAREFLOW_RUNTIME",
    }
    return {
        key: value for key, value in os.environ.items() if key in allowed or key.startswith("LC_")
    }


async def _stop_process_group(process: asyncio.subprocess.Process) -> None:
    """Kill only the session created for this invocation, including CLI descendants."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), timeout=2.0)
    except TimeoutError:
        pass
    finally:
        # The parent may have exited while a descendant ignored SIGTERM.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    await asyncio.wait_for(process.wait(), timeout=2.0)


def _principal_model(body: Mapping[str, Any], requested: str) -> tuple[str, dict[str, Any]]:
    """Bind the principal model to exact top-level usage, retaining any auxiliary usage."""
    fields = {
        "input_tokens": "inputTokens",
        "output_tokens": "outputTokens",
        "cache_read_input_tokens": "cacheReadInputTokens",
        "cache_creation_input_tokens": "cacheCreationInputTokens",
    }
    usage, models = body.get("usage"), body.get("modelUsage")
    if not isinstance(usage, dict) or not isinstance(models, dict) or not models:
        raise ValueError("Claude review has no model usage evidence")
    if any(type(usage.get(key)) is not int or usage[key] < 0 for key in fields):
        raise ValueError("Claude review top-level usage is incomplete")
    matching = []
    for model, details in models.items():
        if (
            not isinstance(model, str)
            or not model.startswith("claude-")
            or not isinstance(details, dict)
            or details.get("provider") != "firstParty"
            or any(type(details.get(key)) is not int or details[key] < 0 for key in fields.values())
        ):
            raise ValueError("Claude review model usage is incomplete or not first-party")
        if all(usage[top] == details[per_model] for top, per_model in fields.items()):
            matching.append(model)
    if matching != [requested] or models[requested].get("canonicalModel") != requested:
        raise ValueError("Claude review principal model did not match request unambiguously")
    return requested, {model: details for model, details in models.items() if model != requested}


class AuditedCLIReviewProvider:
    """ChatProvider for an explicitly selected Claude model and a bounded offline review budget.

    The outer review orchestrator still uses the canonical ReplayProvider. This adapter retains
    raw CLI result objects as evidence and exposes only the structured output to that provider.
    Authentication evidence deliberately excludes email, account IDs, tokens and raw output.
    """

    def __init__(
        self,
        *,
        model_id: str,
        schema: Mapping[str, Any],
        task: str = "eval_judge",
        tool: str = "claude",
        timeout_seconds: float = 120.0,
        max_calls: int = 199,
    ) -> None:
        if tool != "claude" or task != "eval_judge":
            raise ValueError("Audited CLI review requires the configured claude eval_judge")
        if not re.fullmatch(r"claude-[A-Za-z0-9][A-Za-z0-9.-]*", model_id):
            raise ValueError("Review requires an explicit Claude model ID, not an alias")
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
        self._max_calls, self._timeout = max_calls, timeout_seconds
        self._schema = json.dumps(dict(schema), sort_keys=True, separators=(",", ":"))
        self._lock = asyncio.Lock()

    @property
    def remaining_calls(self) -> int:
        return self._max_calls - self.calls_made

    def _offline(self) -> None:
        if os.environ.get("CAREFLOW_RUNTIME") != "offline":
            raise LivePathForbidden("Claude review requires CAREFLOW_RUNTIME=offline")

    def _executable(self) -> str:
        executable = shutil.which("claude", path=_child_environment().get("PATH"))
        if executable is None:
            raise ProviderUnavailable(self.task, "claude is not installed")
        return executable

    async def _run(
        self,
        argv: Sequence[str],
        *,
        input_bytes: bytes | None,
        timeout_seconds: float,
        evidence: dict[str, Any] | None = None,
    ) -> tuple[int, bytes, bytes]:
        with tempfile.TemporaryDirectory(prefix="careflow-review-cli-") as cwd:
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
        """Check the filtered child environment actually resolves subscription authentication."""
        self._offline()
        exit_code, stdout, _ = await self._run(
            [self._executable(), "auth", "status", "--json"],
            input_bytes=None,
            timeout_seconds=min(self._timeout, 15.0),
        )
        try:
            body = json.loads(stdout)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProviderUnavailable(
                self.task, "Claude authentication status was not JSON"
            ) from exc
        if (
            exit_code != 0
            or not isinstance(body, dict)
            or body.get("loggedIn") is not True
            or body.get("authMethod") != "claude.ai"
            or body.get("apiProvider") != "firstParty"
            or body.get("subscriptionType") not in {"pro", "max", "team", "enterprise"}
        ):
            raise ProviderUnavailable(
                self.task, "Claude subscription authentication was not verified"
            )
        self.auth_receipt = {
            key: body[key] for key in ("loggedIn", "authMethod", "apiProvider", "subscriptionType")
        }
        self.auth_receipt["checked_at"] = datetime.now(UTC).isoformat()
        return dict(self.auth_receipt)

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        del temperature, max_tokens  # The CLI controls sampling; receipt records its actual result.
        async with self._lock:
            self._offline()
            self.verified_model_id = None
            if self.remaining_calls <= 0:
                raise ProviderUnavailable(self.task, "Claude review invocation budget exhausted")
            auth = await self.verify_auth()
            argv = [
                self._executable(),
                "-p",
                "--output-format",
                "json",
                "--json-schema",
                self._schema,
                "--model",
                self.model_name,
                "--tools",
                "",
                "--disable-slash-commands",
                "--strict-mcp-config",
                "--mcp-config",
                '{"mcpServers":{}}',
                "--setting-sources",
                "",
                "--no-session-persistence",
            ]
            prompt = render_prompt(messages).encode("utf-8")
            evidence: dict[str, Any] = {
                "started_at": datetime.now(UTC).isoformat(),
                "argv": argv,
                "prompt_sha256": _digest(prompt),
                "requested_model_id": self.model_name,
                "auth": auth,
                "exit_code": None,
                "result": None,
            }
            self.calls_made += 1
            evidence["call_number"] = self.calls_made
            self.raw_cli_evidence.append(evidence)
            try:
                exit_code, stdout, stderr = await self._run(
                    argv, input_bytes=prompt, timeout_seconds=self._timeout, evidence=evidence
                )
                evidence.update(
                    exit_code=exit_code,
                    stdout_sha256=_digest(stdout),
                    stderr_sha256=_digest(stderr),
                )
                try:
                    body = json.loads(stdout)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise ProviderUnavailable(
                        self.task, "Claude review returned invalid JSON"
                    ) from exc
                evidence["result"] = body
                if (
                    exit_code != 0
                    or not isinstance(body, dict)
                    or body.get("is_error") is True
                    or body.get("subtype") != "success"
                ):
                    raise ProviderUnavailable(
                        self.task, "Claude review did not finish successfully"
                    )
                try:
                    principal, auxiliary = _principal_model(body, self.model_name)
                except ValueError as exc:
                    raise ProviderUnavailable(self.task, str(exc)) from exc
                output = body.get("structured_output")
                if not isinstance(output, dict):
                    raise ProviderUnavailable(self.task, "Claude review has no structured output")
                self.verified_model_id = principal
                evidence["verified_model_id"] = self.verified_model_id
                evidence["auxiliary_model_usage"] = auxiliary
                return ChatResult(
                    text=json.dumps(output, ensure_ascii=False), model=self.model_name
                )
            except BaseException as exc:
                evidence.setdefault("failure", type(exc).__name__)
                raise

    async def stream(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        yield (await self.chat(messages)).text


__all__ = ["AuditedCLIReviewProvider"]
