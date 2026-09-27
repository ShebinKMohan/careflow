"""Controlled helper tests: all endpoint observations and inference are replaced locally."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from careflow_providers.base import StructuredFailure
from careflow_providers.config import LocalTaskConfig, ProvidersConfig

SCRIPT = Path(__file__).resolve().parents[1] / "eval_local.py"
spec = importlib.util.spec_from_file_location("eval_local", SCRIPT)
assert spec and spec.loader
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
MODEL, REVISION = helper.pinned_model(helper.WRAPPER.read_text(), {})
BASE_URL = "http://127.0.0.1:8081/v1"
SNAPSHOT = f"/controlled/models--{MODEL.replace('/', '--')}/snapshots/{REVISION}"
START = f"with_llm: starting mlx_lm.server ({MODEL}@{REVISION}) on http://127.0.0.1:8081; log test"
LOG = START + "\nwith_llm: server ready after 1s\n"


def test_wrapper_pins_and_override_semantics():
    wrapper = "default_model=org/Qwen3-test\ndefault_revision=" + "a" * 40 + " # pinned\n"
    assert helper.pinned_model(wrapper, {}) == ("org/Qwen3-test", "a" * 40)
    assert helper.pinned_model(wrapper, {"CAREFLOW_LLM_REVISION": "b" * 40})[1] == "b" * 40
    with pytest.raises(ValueError, match="explicit"):
        helper.pinned_model(wrapper, {"CAREFLOW_LLM_MODEL": "other/Qwen3-test"})
    assert helper.pinned_model(
        wrapper,
        {"CAREFLOW_LLM_MODEL": "other/Qwen3-test", "CAREFLOW_LLM_REVISION": "c" * 40},
    ) == ("other/Qwen3-test", "c" * 40)


@pytest.mark.parametrize(
    "log",
    [
        "",
        START,
        LOG + START,
        LOG + "with_llm: reusing the server already answering on http://127.0.0.1:8081",
        LOG + "with_llm: stopping mlx_lm.server (pid 123)",
        LOG.replace(REVISION, "b" * 40),
        LOG.replace(":8081", ":8082"),
        "with_llm: server ready after 1s\n" + START,
    ],
)
def test_missing_mismatched_reused_or_ended_launch_rejected(log):
    with pytest.raises(ValueError, match="fresh"):
        helper.fresh_launch(log, model=MODEL, revision=REVISION, base_url=BASE_URL)


def test_fresh_launch_and_exact_observed_snapshot():
    assert helper.fresh_launch(LOG, model=MODEL, revision=REVISION, base_url=BASE_URL) == START
    selected, ids = helper.observed_snapshot(
        {"data": [{"id": "default_model"}, {"id": SNAPSHOT}]}, model=MODEL, revision=REVISION
    )
    assert selected == SNAPSHOT
    assert ids == [SNAPSHOT, "default_model"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"data": []},
        {"data": [{"id": "default_model"}]},
        {"data": [{"id": None}]},
        {"data": [{"id": " "}]},
        {"data": [{"id": SNAPSHOT}, {"id": SNAPSHOT}]},
        {"data": [{"id": SNAPSHOT}, {"id": "/second" + SNAPSHOT}]},
        {"data": [{"id": SNAPSHOT.replace(REVISION, "b" * 40)}]},
    ],
)
def test_ambiguous_or_unidentified_snapshot_rejected(body):
    with pytest.raises(ValueError):
        helper.observed_snapshot(body, model=MODEL, revision=REVISION)


@pytest.mark.parametrize(
    "argv",
    [[], ["unknown"], ["baseline"], ["review", "--launch-log", "a", "--batch-size", "0"]],
)
def test_bad_invocations_fail_before_any_work(argv):
    with pytest.raises(SystemExit) as caught:
        helper.main(argv)
    assert caught.value.code == 2


def test_direct_invocation_requires_wrapper_environment(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("CAREFLOW_LOCAL_LLM_BASE_URL", raising=False)
    assert helper.main(["baseline", "--launch-log", str(tmp_path / "missing")]) == 1
    assert "scripts/with_llm.sh" in capsys.readouterr().err


@pytest.fixture
def local_setup(monkeypatch, tmp_path):
    monkeypatch.setenv("CAREFLOW_LOCAL_LLM_BASE_URL", BASE_URL)
    monkeypatch.delenv("CAREFLOW_LLM_MODEL", raising=False)
    monkeypatch.delenv("CAREFLOW_LLM_REVISION", raising=False)
    config = ProvidersConfig(
        tasks={"extraction": LocalTaskConfig(provider="local", model=MODEL, base_url=BASE_URL)}
    )
    monkeypatch.setattr(helper, "providers_config", lambda path: config)
    log = tmp_path / "launch.log"
    log.write_text(LOG)
    return ["--launch-log", str(log), "--output-root", str(tmp_path / "reports")], config


@pytest.mark.parametrize("status,exit_code", [("completed", 0), ("incomplete", 1)])
async def test_baseline_forwards_real_interfaces_and_retains_evidence(
    monkeypatch, tmp_path, local_setup, capsys, status, exit_code
):
    common, _ = local_setup
    warmed = {"duration_ms": 3.0, "included_in_case_latencies": False}
    monkeypatch.setattr(helper, "warmup", AsyncMock(return_value=warmed))
    report = {
        "schema_version": 1,
        "suite": "extraction_en",
        "status": status,
        "metadata": {},
        "results": [],
        "metrics": {"field_f1": {"value": 0.5}},
    }
    runner = AsyncMock(return_value=report)
    monkeypatch.setattr(helper, "run_suite", runner)
    args = helper.parser().parse_args(
        [
            "baseline",
            *common,
            "--record-root",
            str(tmp_path / "recordings"),
            "--dataset",
            str(tmp_path / "cases"),
            "--timeout",
            "27",
        ]
    )
    with respx.mock() as router:
        endpoint = router.get(BASE_URL + "/models").respond(200, json={"data": [{"id": SNAPSHOT}]})
        assert await helper.run(args) == exit_code
    assert endpoint.call_count == 2
    call = runner.call_args
    assert call.args == ("extraction_en",)
    assert call.kwargs["record_dir"] == tmp_path / "recordings"
    assert call.kwargs["dataset_path"] == tmp_path / "cases"
    assert call.kwargs["timeout_seconds"] == 27
    assert call.kwargs["provider_config"].tasks["extraction"].served_model == SNAPSHOT
    assert call.kwargs["model_receipt"]["server_model_ids"] == [SNAPSHOT]
    [evidence] = (tmp_path / "reports/local-runs").iterdir()
    assert (evidence / "launch.log").read_text() == LOG
    assert json.loads((evidence / "warmup.json").read_text()) == warmed
    assert json.loads((evidence / "model-receipt.json").read_text())["revision"] == REVISION
    assert (tmp_path / "reports/extraction_en/LATEST.md").exists()
    assert "not a quality or clinical pass" in capsys.readouterr().out


async def test_review_forwards_options_and_rejection_fails(monkeypatch, tmp_path, local_setup):
    common, _ = local_setup
    reviewer = AsyncMock(return_value={"status": "review_failed", "accepted": 1, "rejected": 1})
    monkeypatch.setattr(helper, "review_dataset", reviewer)
    args = helper.parser().parse_args(
        [
            "review",
            *common,
            "--batch-size",
            "2",
            "--timeout",
            "60",
            "--dataset",
            str(tmp_path / "cases"),
        ]
    )
    with respx.mock() as router:
        router.get(BASE_URL + "/models").respond(200, json={"data": [{"id": SNAPSHOT}]})
        assert await helper.run(args) == 1
    assert reviewer.call_args.kwargs["dataset_path"] == tmp_path / "cases"
    assert reviewer.call_args.kwargs["batch_size"] == 2
    assert reviewer.call_args.kwargs["timeout_seconds"] == 60
    assert reviewer.call_args.kwargs["config_path"].exists()
    assert reviewer.call_args.kwargs["model_receipt"]["served_model"] == SNAPSHOT


async def test_warmup_uses_empty_production_messages_and_schema(monkeypatch, local_setup):
    _, config = local_setup
    provider = object()
    monkeypatch.setattr(helper, "build_provider", lambda task, actual: provider)
    structured = AsyncMock(return_value=SimpleNamespace(raw_text='{"facts":[]}'))
    monkeypatch.setattr(helper, "structured", structured)
    result = await helper.warmup(config, 1)
    assert structured.call_args.args == (
        provider,
        helper.build_messages([], {}),
        helper.ExtractionOutput,
    )
    assert structured.call_args.kwargs == {"max_tokens": 100}
    assert result["included_in_case_latencies"] is False
    assert result["raw_text"] == '{"facts":[]}'


@pytest.mark.parametrize("timed_out", [False, True])
async def test_failed_warmup_stops_before_baseline(monkeypatch, local_setup, tmp_path, timed_out):
    common, _ = local_setup
    monkeypatch.setattr(helper, "build_provider", lambda task, config: object())
    structured = AsyncMock(
        side_effect=TimeoutError("controlled timeout") if timed_out else None,
        return_value=StructuredFailure(task="extraction", errors=["invalid"], raw_text="invalid"),
    )
    monkeypatch.setattr(helper, "structured", structured)
    runner = AsyncMock()
    monkeypatch.setattr(helper, "run_suite", runner)
    with respx.mock() as router:
        router.get(BASE_URL + "/models").respond(200, json={"data": [{"id": SNAPSHOT}]})
        with pytest.raises((ValueError, TimeoutError)):
            await helper.run(helper.parser().parse_args(["baseline", *common]))
    runner.assert_not_called()
    [failure] = (tmp_path / "reports/local-runs").glob("*/failure.json")
    assert json.loads(failure.read_text())["status"] == "failed"


async def test_nonlocal_config_rejected_before_endpoint(monkeypatch, local_setup):
    common, _ = local_setup
    config = ProvidersConfig.model_validate(
        {"tasks": {"extraction": {"provider": "api", "vendor": "openai", "model": "test-only"}}}
    )
    monkeypatch.setattr(helper, "providers_config", lambda path: config)
    with respx.mock() as router:
        with pytest.raises(ValueError, match="local extraction"):
            await helper.run(helper.parser().parse_args(["baseline", *common]))
        assert not router.calls


def test_endpoint_network_failure_returns_nonzero(local_setup, capsys):
    common, _ = local_setup
    with respx.mock() as router:
        router.get(BASE_URL + "/models").mock(side_effect=httpx.ConnectError("controlled offline"))
        assert helper.main(["baseline", *common]) == 1
    assert "controlled offline" in capsys.readouterr().err
