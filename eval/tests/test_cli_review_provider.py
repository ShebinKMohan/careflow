"""Subprocess-boundary tests; these never invoke Claude or an authentication service."""

from __future__ import annotations

import asyncio
import copy
import json
import signal
from pathlib import Path
from typing import Any

import pytest

from careflow_eval.cli_review_provider import AuditedCLIReviewProvider
from careflow_eval.review import ReviewBatch
from careflow_providers.base import Message, StructuredOk, structured
from careflow_providers.errors import LivePathForbidden, ProviderUnavailable

MODEL = "claude-sonnet-5"
AUTH = {
    "loggedIn": True,
    "authMethod": "claude.ai",
    "apiProvider": "firstParty",
    "subscriptionType": "max",
    "email": "private@example.invalid",
    "orgId": "private-org",
}
MESSAGES: list[Message] = [{"role": "user", "content": "Synthetic review prompt only."}]


def _result() -> dict[str, Any]:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "structured_output": {"verdicts": []},
        "usage": {
            "input_tokens": 2,
            "output_tokens": 52,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 5355,
        },
        "modelUsage": {
            MODEL: {
                "inputTokens": 2,
                "outputTokens": 52,
                "cacheReadInputTokens": 0,
                "cacheCreationInputTokens": 5355,
                "canonicalModel": MODEL,
                "provider": "firstParty",
            },
            "claude-haiku-4-5-20251001": {
                "inputTokens": 906,
                "outputTokens": 13,
                "cacheReadInputTokens": 0,
                "cacheCreationInputTokens": 0,
                "canonicalModel": "claude-haiku-4-5",
                "provider": "firstParty",
            },
        },
    }


class FakeProcess:
    pid = 123456

    def __init__(self, body: Any, *, code: int = 0, blocked: bool = False) -> None:
        self.stdout = json.dumps(body).encode()
        self.returncode: int | None = code if not blocked else None
        self.blocked = blocked
        self.input: bytes | None = None
        self.started = asyncio.Event()

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        self.input = input
        self.started.set()
        if self.blocked:
            await asyncio.sleep(100)
        return self.stdout, b"private stderr excluded from evidence"

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = -15
        return self.returncode


def _provider(**kwargs: Any) -> AuditedCLIReviewProvider:
    return AuditedCLIReviewProvider(model_id=MODEL, schema={"type": "object"}, **kwargs)


def _mock_processes(
    monkeypatch: pytest.MonkeyPatch, processes: list[FakeProcess]
) -> list[dict[str, Any]]:
    observed = []
    monkeypatch.setenv("CAREFLOW_RUNTIME", "offline")
    monkeypatch.setattr(
        "careflow_eval.cli_review_provider.shutil.which", lambda *a, **k: "/bin/claude-test"
    )

    async def spawn(*argv: str, **kwargs: Any) -> FakeProcess:
        assert Path(kwargs["cwd"]).is_dir()  # noqa: ASYNC240 — tiny local test fixture
        assert list(Path(kwargs["cwd"]).iterdir()) == []  # noqa: ASYNC240
        observed.append({"argv": list(argv), **kwargs})
        return processes.pop(0)

    monkeypatch.setattr("careflow_eval.cli_review_provider.asyncio.create_subprocess_exec", spawn)
    return observed


async def test_subprocess_isolated_subscription_only_and_actual_principal_attributed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    denied = (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "CLAUDECODE",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_ENV_FILE",
        "NODE_OPTIONS",
        "OPENAI_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AZURE_OPENAI_API_KEY",
        "HF_TOKEN",
        "CLAUDE_CONFIG_DIR",
        "HTTPS_PROXY",
    )
    for key in denied:
        monkeypatch.setenv(key, "must-not-reach-child")
    auth, model = FakeProcess(AUTH), FakeProcess(_result())
    observed = _mock_processes(monkeypatch, [auth, model])
    provider = _provider()
    reply = await provider.chat(MESSAGES)
    assert reply.model == provider.verified_model_id == MODEL
    assert json.loads(reply.text) == {"verdicts": []}
    assert provider.calls_made == 1 and provider.remaining_calls == 198
    argv = observed[1]["argv"]
    assert argv[1:5] == ["-p", "--output-format", "json", "--json-schema"]
    for flag in ("--tools", "--setting-sources"):
        assert argv[argv.index(flag) + 1] == ""
    for flag in ("--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"):
        assert flag in argv
    assert argv[argv.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert argv[argv.index("--model") + 1] == MODEL
    assert "Synthetic review prompt only." not in " ".join(argv)
    assert model.input and b"Synthetic review prompt only." in model.input
    for child in observed:
        assert child["start_new_session"] is True
        assert not set(denied) & child["env"].keys()
        assert "HOME" in child["env"]
        assert not Path(child["cwd"]).exists()  # noqa: ASYNC240 — tiny local test fixture
    evidence = provider.raw_cli_evidence[0]
    assert evidence["result"] == _result()
    assert evidence["exit_code"] == 0
    assert set(evidence["auxiliary_model_usage"]) == {"claude-haiku-4-5-20251001"}
    assert "private@example.invalid" not in json.dumps(evidence)
    assert "private-org" not in json.dumps(evidence)
    assert "private stderr" not in json.dumps(evidence)


async def test_live_runtime_is_forbidden_before_any_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = _mock_processes(monkeypatch, [])
    monkeypatch.setenv("CAREFLOW_RUNTIME", "live")
    with pytest.raises(LivePathForbidden):
        await _provider().chat(MESSAGES)
    assert observed == []


@pytest.mark.parametrize(
    "changed",
    [
        {"loggedIn": False},
        {"authMethod": "api_key"},
        {"apiProvider": "bedrock"},
        {"subscriptionType": None},
    ],
)
async def test_unverified_subscription_never_invokes_model(
    monkeypatch: pytest.MonkeyPatch, changed: dict[str, Any]
) -> None:
    observed = _mock_processes(monkeypatch, [FakeProcess({**AUTH, **changed})])
    provider = _provider()
    with pytest.raises(ProviderUnavailable, match="subscription authentication"):
        await provider.chat(MESSAGES)
    assert len(observed) == 1
    assert provider.calls_made == 0
    assert provider.raw_cli_evidence == []


@pytest.mark.parametrize(
    "mutation", ["ambiguous", "wrong-principal", "wrong-canonical", "api", "missing", "no-output"]
)
async def test_model_evidence_fails_closed(monkeypatch: pytest.MonkeyPatch, mutation: str) -> None:
    body = _result()
    if mutation == "ambiguous":
        body["modelUsage"]["claude-other"] = copy.deepcopy(body["modelUsage"][MODEL])
    elif mutation == "wrong-principal":
        body["modelUsage"]["claude-other"] = body["modelUsage"].pop(MODEL)
    elif mutation == "wrong-canonical":
        body["modelUsage"][MODEL]["canonicalModel"] = "claude-other"
    elif mutation == "api":
        body["modelUsage"][MODEL]["provider"] = "bedrock"
    elif mutation == "missing":
        del body["usage"]["cache_creation_input_tokens"]
    else:
        del body["structured_output"]
    _mock_processes(monkeypatch, [FakeProcess(AUTH), FakeProcess(body)])
    provider = _provider()
    with pytest.raises(ProviderUnavailable):
        await provider.chat(MESSAGES)
    assert provider.verified_model_id is None
    assert provider.calls_made == 1
    assert provider.raw_cli_evidence[0]["failure"] == "ProviderUnavailable"


async def test_budget_counts_each_model_invocation_and_does_not_launch_after_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = _mock_processes(monkeypatch, [FakeProcess(AUTH), FakeProcess(_result())])
    provider = _provider(max_calls=1)
    await provider.chat(MESSAGES)
    with pytest.raises(ProviderUnavailable, match="budget exhausted"):
        await provider.chat(MESSAGES)
    assert provider.verified_model_id is None
    assert provider.remaining_calls == 0
    assert len(observed) == 2


async def test_structured_retry_consumes_another_call_and_preserves_both_raw_replies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = _result()
    invalid["structured_output"] = {"verdicts": "invalid"}
    observed = _mock_processes(
        monkeypatch,
        [FakeProcess(AUTH), FakeProcess(invalid), FakeProcess(AUTH), FakeProcess(_result())],
    )
    provider = _provider(max_calls=2)
    result = await structured(provider, MESSAGES, ReviewBatch)
    assert isinstance(result, StructuredOk)
    assert result.value.verdicts == []
    assert provider.calls_made == 2 and provider.remaining_calls == 0
    assert len(observed) == 4
    assert [entry["call_number"] for entry in provider.raw_cli_evidence] == [1, 2]
    assert provider.raw_cli_evidence[0]["result"]["structured_output"] == {"verdicts": "invalid"}
    assert (
        provider.raw_cli_evidence[0]["prompt_sha256"]
        != provider.raw_cli_evidence[1]["prompt_sha256"]
    )


@pytest.mark.parametrize("cancel", [False, True])
async def test_timeout_and_cancellation_kill_only_own_process_group(
    monkeypatch: pytest.MonkeyPatch,
    cancel: bool,
) -> None:
    blocked = FakeProcess({}, blocked=True)
    _mock_processes(monkeypatch, [FakeProcess(AUTH), blocked])
    killed: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(
        "careflow_eval.cli_review_provider.os.killpg", lambda pid, sig: killed.append((pid, sig))
    )
    provider = _provider(timeout_seconds=0.02)
    task = asyncio.create_task(provider.chat(MESSAGES))
    if cancel:
        await blocked.started.wait()
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
        await task
    assert killed == [(blocked.pid, signal.SIGTERM), (blocked.pid, signal.SIGKILL)]
    assert provider.calls_made == 1
    assert provider.raw_cli_evidence[0]["exit_code"] == -15
    assert provider.raw_cli_evidence[0]["failure"] == (
        "CancelledError" if cancel else "TimeoutError"
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"tool": "codex"},
        {"task": "extraction"},
        {"max_calls": 200},
        {"max_calls": True},
        {"max_calls": 0},
        {"timeout_seconds": float("nan")},
    ],
)
def test_invalid_review_scope_rejected(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _provider(**kwargs)
