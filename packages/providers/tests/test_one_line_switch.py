"""Switching extraction from the local model to an API is one line of config/providers.yaml (M1-19).

The copy differs from the real file in exactly the `extraction:` line; the intake's real ports
built from it use the API adapter; and an API reply carrying the text the local Qwen3.5-9B model
really returned (recorded in eval/replays by M1-18) gives the same facts as replaying that local
reply. `OPENAI_API_KEY` is a dummy and the API is mocked with respx: no key, no network.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

import httpx
import pytest
import respx

from careflow_eval.datasets import load_dataset, transcript
from careflow_eval.runner import DEFAULT_DATASET, REPO
from careflow_intake.extraction.extractor import LlmExtractor
from careflow_intake.wiring import RealPortsSettings, build_real_ports
from careflow_providers.adapters.api import ApiProvider
from careflow_providers.adapters.replay import ReplayProvider

CONFIG = REPO / "config" / "providers.yaml"
CASE = "routine-01-wrist-ache"
RECORDED = REPO / "eval" / "replays" / "extraction_en" / f"{CASE}.cassette.json"
API_LINE = "  extraction:   { provider: api, vendor: openai, model: gpt-4.1-mini }\n"


def switched(tmp_path: Path) -> tuple[Path, list[str]]:
    original = CONFIG.read_text(encoding="utf-8").splitlines(keepends=True)
    changed = [API_LINE if line.startswith("  extraction:") else line for line in original]
    path = tmp_path / "providers.yaml"
    path.write_text("".join(changed), encoding="utf-8")
    diff = [
        line
        for line in difflib.unified_diff(original, changed, n=0)
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    ]
    return path, diff


def test_the_switch_is_one_changed_line(tmp_path: Path) -> None:
    _, diff = switched(tmp_path)
    assert diff == [
        "-  extraction:   { provider: local, model: qwen3.5-9b-4bit }\n",
        f"+{API_LINE}",
    ]


@respx.mock
async def test_the_api_adapter_gives_the_local_models_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CAREFLOW_PROVIDER_MODE", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-dummy")
    path, _ = switched(tmp_path)
    ports = build_real_ports(RealPortsSettings(providers=path, llm_phrasing=False))
    api_extractor = ports.extractor
    assert isinstance(api_extractor, LlmExtractor)
    assert isinstance(api_extractor.provider, ApiProvider)
    assert (api_extractor.provider_kind, api_extractor.provider.model_name) == (
        "api",
        "gpt-4.1-mini",
    )

    [recorded] = json.loads(RECORDED.read_text(encoding="utf-8")).values()
    scenario = next(s for s in load_dataset(DEFAULT_DATASET).scenarios if s.id == CASE)
    turns, keys = transcript(scenario)
    local = LlmExtractor(
        ReplayProvider(
            None, task="extraction", model=recorded["model"], cassette=RECORDED, mode="replay"
        ),
        provider_kind="local",
    )
    from_local = await local.extract(turns, [], keys, scenario.language)

    completion = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-4.1-mini",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": recorded["text"]},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    route = respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=completion)
    )
    from_api = await api_extractor.extract(turns, [], keys, scenario.language)

    assert route.call_count == 1
    assert not from_local.extraction_incomplete and from_local.facts
    assert [f.model_dump(mode="json") for f in from_api.facts] == [
        f.model_dump(mode="json") for f in from_local.facts
    ]
    assert (from_api.provider, from_local.provider) == ("api", "local")
