"""`CAREFLOW_INTAKE_CONFIG` points the intake at another config file (deterministic runs), and a
replayed extraction provider is reported as `replay`, never as the model it stands in for."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from careflow_intake.extraction.extractor import LlmExtractor
from careflow_intake.wiring import INTAKE_CONFIG, intake_config_path, load_intake_config
from careflow_providers.config import parse_config


def test_the_intake_config_path_can_be_overridden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CAREFLOW_INTAKE_CONFIG", raising=False)
    assert intake_config_path() == INTAKE_CONFIG
    deterministic = tmp_path / "intake.yaml"
    deterministic.write_text("llm_phrasing: false\nbriefing_summary: false\n")
    monkeypatch.setenv("CAREFLOW_INTAKE_CONFIG", str(deterministic))
    config = load_intake_config()
    assert (config.llm_phrasing, config.briefing_summary) == (False, False)


def test_a_replayed_extractor_says_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cassette = tmp_path / "cassette.json"
    cassette.write_text(json.dumps({}))
    monkeypatch.setenv("CAREFLOW_PROVIDER_MODE", "replay")
    monkeypatch.setenv("CAREFLOW_CASSETTE", str(cassette))
    config = parse_config({"tasks": {"extraction": {"provider": "local", "model": "qwen-test"}}})
    assert LlmExtractor.from_config(config).provider_kind == "replay"
    monkeypatch.setenv("CAREFLOW_PROVIDER_MODE", "record")
    assert LlmExtractor.from_config(config).provider_kind == "local"
