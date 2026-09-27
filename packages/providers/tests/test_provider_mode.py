"""`CAREFLOW_PROVIDER_MODE`: build_provider records or replays every task (M1-12, M1-19)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from careflow_providers import CassetteMiss, Message, ReplayProvider
from careflow_providers.adapters.local import LocalProvider
from careflow_providers.adapters.replay import cassette_key
from careflow_providers.config import parse_config
from careflow_providers.factory import build_provider

MESSAGES: list[Message] = [{"role": "user", "content": "I have had chest pain since this morning"}]
CONFIG = parse_config(
    {
        "tasks": {
            "extraction": {"provider": "local", "model": "qwen3.5-9b-4bit"},
            "summary": {"provider": "api", "vendor": "openai", "model": "gpt-test"},
        }
    }
)


@pytest.fixture
def cassette(tmp_path: Path) -> Path:
    path = tmp_path / "cassette.json"
    key = cassette_key("extraction", "qwen3.5-9b-4bit", MESSAGES, 0.0)
    path.write_text(json.dumps({key: {"model": "qwen3.5-9b-4bit", "text": '{"facts": []}'}}))
    return path


def test_off_builds_the_configured_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAREFLOW_PROVIDER_MODE", raising=False)
    assert isinstance(build_provider("extraction", CONFIG), LocalProvider)


async def test_replay_answers_only_from_the_cassette(
    monkeypatch: pytest.MonkeyPatch, cassette: Path
) -> None:
    monkeypatch.setenv("CAREFLOW_PROVIDER_MODE", "replay")
    monkeypatch.setenv("CAREFLOW_CASSETTE", str(cassette))
    provider = build_provider("extraction", CONFIG)
    assert isinstance(provider, ReplayProvider) and provider.mode == "replay"
    assert (await provider.chat(MESSAGES)).text == '{"facts": []}'
    with pytest.raises(CassetteMiss):  # anything not recorded fails loudly, never calls a model
        await provider.chat([{"role": "user", "content": "something else"}])


def test_replay_needs_no_key_for_an_api_task(
    monkeypatch: pytest.MonkeyPatch, cassette: Path
) -> None:
    monkeypatch.setenv("CAREFLOW_PROVIDER_MODE", "replay")
    monkeypatch.setenv("CAREFLOW_CASSETTE", str(cassette))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = build_provider("summary", CONFIG)
    assert isinstance(provider, ReplayProvider) and provider.model_name == "gpt-test"


def test_replay_without_a_cassette_fails_at_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAREFLOW_PROVIDER_MODE", "replay")
    monkeypatch.delenv("CAREFLOW_CASSETTE", raising=False)
    with pytest.raises(ValueError, match="cassette"):
        build_provider("extraction", CONFIG)


def test_record_wraps_the_configured_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CAREFLOW_PROVIDER_MODE", "record")
    monkeypatch.setenv("CAREFLOW_CASSETTE", str(tmp_path / "new.json"))
    provider = build_provider("extraction", CONFIG)
    assert isinstance(provider, ReplayProvider) and provider.mode == "record"
