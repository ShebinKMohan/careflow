"""Subprocess-boundary tests of the audited Codex reviewer (M2-03). They never invoke Codex: fake
processes answer, and a fake Codex home holds the session log the identity is read from."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from careflow_eval.codex_review_provider import AuditedCodexReviewProvider, codex_identity
from careflow_providers.base import Message
from careflow_providers.errors import LivePathForbidden, ProviderUnavailable

MODEL = "gpt-6-astra"
THREAD = "01a0e6d5-6459-7b00-80fd-c17a23f8e245"
MESSAGES: list[Message] = [{"role": "user", "content": "Synthetic review prompt only."}]
OUTPUT = {"verdicts": []}


def _session(home: Path, *, model: str = MODEL, effort: str = "high", provider: str = "openai"):
    folder = home / "sessions" / "2026" / "09" / "28"
    folder.mkdir(parents=True, exist_ok=True)
    events = [
        {
            "type": "session_meta",
            "payload": {
                "id": THREAD,
                "model_provider": provider,
                "cli_version": "0.154.0",
                "originator": "codex_exec",
                "source": "exec",
                "creator_account_id": "private-account",
                "creator_user_id": "private-user",
            },
        },
        {
            "type": "turn_context",
            "payload": {
                "model": model,
                "effort": effort,
                "sandbox_policy": {"type": "read-only"},
                "approval_policy": "never",
                "cwd": "/private/tmp/somewhere",
            },
        },
    ]
    path = folder / f"rollout-2026-09-28T12-35-39-{THREAD}.jsonl"
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")


def _events(*, failed: bool = False) -> bytes:
    events: list[dict[str, Any]] = [
        {"type": "thread.started", "thread_id": THREAD},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(OUTPUT)}},
        {"type": "turn.failed"} if failed else {"type": "turn.completed", "usage": {"x": 1}},
    ]
    return "\n".join(json.dumps(event) for event in events).encode()


class FakeProcess:
    pid = 123456

    def __init__(self, stdout: bytes, *, code: int = 0, write: Any = None) -> None:
        self.stdout, self.returncode, self.write = stdout, code, write
        self.input: bytes | None = None

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        self.input = input
        return self.stdout, b"private stderr excluded from evidence"


def _mock(
    monkeypatch: pytest.MonkeyPatch, processes: list[FakeProcess], *, output: Any = OUTPUT
) -> list[dict[str, Any]]:
    observed: list[dict[str, Any]] = []
    monkeypatch.setenv("CAREFLOW_RUNTIME", "offline")
    monkeypatch.setattr(
        "careflow_eval.codex_review_provider.shutil.which", lambda *a, **k: "/bin/codex-test"
    )

    async def spawn(*argv: str, **kwargs: Any) -> FakeProcess:
        observed.append({"argv": list(argv), **kwargs})
        process = processes.pop(0)
        if "--output-last-message" in argv and output is not None:
            last = argv[argv.index("--output-last-message") + 1]
            Path(last).write_text(json.dumps(output), encoding="utf-8")  # noqa: ASYNC240
        return process

    monkeypatch.setattr("careflow_eval.codex_review_provider.asyncio.create_subprocess_exec", spawn)
    return observed


def _provider(home: Path, **kwargs: Any) -> AuditedCodexReviewProvider:
    return AuditedCodexReviewProvider(
        model_id=MODEL, schema={"type": "object"}, home=home, **kwargs
    )


LOGGED_IN = b"Logged in using ChatGPT\n"


async def test_codex_review_is_isolated_and_its_model_comes_from_the_session_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "HTTPS_PROXY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(key, "must-not-reach-child")
    _session(tmp_path)
    observed = _mock(monkeypatch, [FakeProcess(LOGGED_IN), FakeProcess(_events())])
    provider = _provider(tmp_path)
    reply = await provider.chat(MESSAGES)
    assert json.loads(reply.text) == OUTPUT
    assert reply.model == provider.verified_model_id == MODEL
    assert provider.calls_made == 1 and provider.remaining_calls == 198
    argv = observed[1]["argv"]
    assert argv[1:3] == ["exec", "--json"]
    for flag in ("--skip-git-repo-check", "--ignore-user-config", "--ignore-rules"):
        assert flag in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert argv[argv.index("--model") + 1] == MODEL
    assert argv[argv.index("--config") + 1] == 'model_reasoning_effort="high"'
    assert argv[-1] == "-"  # the prompt goes through stdin, never argv
    assert "Synthetic review prompt only." not in " ".join(argv)
    assert observed[1]["start_new_session"] is True
    for child in observed:
        assert not {"OPENAI_API_KEY", "CODEX_API_KEY", "HTTPS_PROXY"} & child["env"].keys()
        assert "HOME" in child["env"]
    evidence = json.dumps(provider.raw_cli_evidence)
    assert THREAD in evidence and '"model": "gpt-6-astra"' in evidence
    for private in ("private-account", "private-user", "private stderr", "/private/tmp"):
        assert private not in evidence


@pytest.mark.parametrize(
    ("session", "reason"),
    [
        ({"model": "gpt-5-mini"}, "requested model"),
        ({"effort": "low"}, "requested model"),
        ({"provider": "azure"}, "first-party"),
    ],
)
async def test_a_different_model_in_the_session_log_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session: dict[str, str], reason: str
) -> None:
    _session(tmp_path, **session)
    _mock(monkeypatch, [FakeProcess(LOGGED_IN), FakeProcess(_events())])
    provider = _provider(tmp_path)
    with pytest.raises(ProviderUnavailable, match=reason):
        await provider.chat(MESSAGES)
    assert provider.verified_model_id is None
    # The evidence keeps what actually ran, for the audit.
    recorded = provider.raw_cli_evidence[0]["identity"]
    assert recorded["turns"][0]["model"] == session.get("model", MODEL)
    assert recorded["session"]["model_provider"] == session.get("provider", "openai")


async def test_a_failed_turn_or_missing_log_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock(monkeypatch, [FakeProcess(LOGGED_IN), FakeProcess(_events(failed=True))])
    with pytest.raises(ProviderUnavailable, match="did not complete"):
        await _provider(tmp_path).chat(MESSAGES)
    _mock(monkeypatch, [FakeProcess(LOGGED_IN), FakeProcess(_events())])
    with pytest.raises(ProviderUnavailable, match="not found"):
        await _provider(tmp_path).chat(MESSAGES)  # no session log in this home


async def test_an_api_key_login_never_reaches_a_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _session(tmp_path)
    observed = _mock(monkeypatch, [FakeProcess(b"Logged in using an API key\n")])
    with pytest.raises(ProviderUnavailable, match="subscription login"):
        await _provider(tmp_path).chat(MESSAGES)
    assert len(observed) == 1  # only `codex login status`


async def test_the_live_runtime_and_an_exhausted_budget_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _session(tmp_path)
    monkeypatch.setenv("CAREFLOW_RUNTIME", "live")
    with pytest.raises(LivePathForbidden):
        await _provider(tmp_path).chat(MESSAGES)
    _mock(monkeypatch, [FakeProcess(LOGGED_IN), FakeProcess(_events())])
    provider = _provider(tmp_path, max_calls=1)
    await provider.chat(MESSAGES)
    with pytest.raises(ProviderUnavailable, match="budget"):
        await provider.chat(MESSAGES)


@pytest.mark.parametrize(
    "kwargs",
    [{"model_id": "claude-sonnet-5"}, {"model_id": "default"}, {"effort": "ultra"}, {"tool": "x"}],
)
def test_invalid_codex_review_scope_is_rejected(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        AuditedCodexReviewProvider(**{"model_id": MODEL, "schema": {}, **kwargs})


def test_the_identity_reader_never_returns_account_ids(tmp_path: Path) -> None:
    _session(tmp_path)
    identity = codex_identity(THREAD, home=tmp_path)
    assert identity["session"]["model_provider"] == "openai"
    assert identity["turns"] == [
        {"model": MODEL, "effort": "high", "sandbox": "read-only", "approval_policy": "never"}
    ]
    assert "private" not in json.dumps(identity)
    with pytest.raises(ValueError, match="UUID"):
        codex_identity("../../etc/passwd", home=tmp_path)
