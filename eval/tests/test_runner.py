"""Real extraction/rules wiring, without a network or a database."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from careflow_eval.providers import ReceiptError
from careflow_eval.runner import run_suite, source_provenance
from careflow_intake.extraction.record import ScriptedChat
from careflow_providers.adapters.local import LocalProvider
from careflow_providers.base import ChatResult, Message


def _dataset(root: Path, *, chest: bool = False) -> Path:
    root.mkdir()
    key, text, gold = (
        ("chest_pain.present", "Yes.", "yes")
        if chest
        else ("chief_complaint", "Dry lips", "Dry lips")
    )
    body = {
        "synthetic": True,
        "id": "synthetic-one",
        "category": "chest_pain" if chest else "routine",
        "description": "Invented evaluation scenario.",
        "patient": {"age_years": 30, "sex": "female"},
        "turns": [{"question_key": key, "patient_text": text}],
        "expected": {
            "facts": {key: gold},
            "alerts": ["CHEST_PAIN_ECG_10MIN"] if chest else [],
            "level": 2 if chest else 5,
            "level_rationale": "Synthetic test label.",
        },
        "generated_by": {"tool": "unit-test", "model": "test-writer", "date": "2026-09-27"},
    }
    (root / "synthetic-one.yaml").write_text(json.dumps(body))
    return root


def _reply(key: str = "chief_complaint", value: Any = "Dry lips", quote: str = "Dry lips") -> str:
    return json.dumps({"facts": [{"key": key, "value": value, "quote": quote, "turn_id": "t2"}]})


async def test_rules_runs_all_56_without_a_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("pure rules must not construct a model")

    monkeypatch.setattr("careflow_eval.runner.build_provider", forbidden)
    result = await run_suite("rules")
    assert result["status"] == "completed"
    assert result["metadata"]["case_count"] == 56
    assert result["metadata"]["rules_validation"] is True
    assert result["failed_ids"] == []
    assert result["metrics"]["alert_recall"]["value"] == 1.0
    assert result["metadata"]["provider"] is None


async def test_real_extractor_scores_generated_enums_and_reports_progress(tmp_path: Path) -> None:
    progress = []
    result = await run_suite(
        "extraction_en",
        dataset_path=_dataset(tmp_path / "cases", chest=True),
        provider=ScriptedChat([_reply("chest_pain.present", True, "Yes.")]),
        include_unreviewed=True,
        progress=lambda *args: progress.append(args),
    )
    row = result["results"][0]
    assert row["prediction"]["facts"] == [{"key": "chest_pain.present", "value": "yes"}]
    assert row["expected"]["alerts"] == ["CHEST_PAIN_ECG_10MIN"]
    assert result["metrics"]["field_f1"]["value"] == 1.0
    assert row["quality_passed"] is True
    assert progress == [("synthetic-one", 1, 1)]


async def test_gold_never_reaches_model_or_predicted_rules(tmp_path: Path) -> None:
    captured: list[Message] = []

    class InspectingChat(ScriptedChat):
        async def chat(
            self,
            messages: Sequence[Message],
            *,
            temperature: float = 0,
            max_tokens: int | None = None,
        ) -> ChatResult:
            captured.extend(messages)
            return await super().chat(messages, temperature=temperature, max_tokens=max_tokens)

    result = await run_suite(
        "extraction_en",
        dataset_path=_dataset(tmp_path / "cases", chest=True),
        provider=InspectingChat(['{"facts":[]}']),
        include_unreviewed=True,
    )
    row = result["results"][0]
    user_prompt = next(m["content"] for m in captured if m["role"] == "user")
    assert "Yes." in user_prompt
    assert "Synthetic test label" not in user_prompt
    assert "CHEST_PAIN_ECG_10MIN" not in user_prompt
    assert row["prediction"]["facts"] == []
    assert row["prediction"]["alerts"] == []
    assert row["rules_validation"] is False
    assert row["quality_passed"] is False
    assert result["metrics"]["alert_recall"]["value"] == 0.0
    assert result["failed_ids"] == ["synthetic-one"]


async def test_wrong_fact_marks_failure_even_if_alerts_match(tmp_path: Path) -> None:
    result = await run_suite(
        "extraction_en",
        dataset_path=_dataset(tmp_path / "cases"),
        provider=ScriptedChat([_reply(value="headache")]),
        include_unreviewed=True,
    )
    assert result["results"][0]["rules_validation"] is True
    assert result["results"][0]["field_f1"] == 0.0
    assert result["failed_ids"] == ["synthetic-one"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"suite": "unknown"},
        {"suite": "rules", "timeout_seconds": 0},
        {"suite": "rules", "timeout_seconds": float("nan")},
        {"suite": "rules", "record_dir": Path("a"), "replay_dir": Path("b")},
    ],
)
async def test_invalid_run_options_fail_before_execution(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        await run_suite(**kwargs)


def test_source_fingerprint_tracks_uncommitted_code_but_not_reports_or_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("careflow_eval.runner.REPO", tmp_path)
    monkeypatch.setattr("careflow_eval.runner.git_sha", lambda: "f" * 40)
    monkeypatch.setattr("careflow_eval.runner.shutil.which", lambda executable: None)
    source = tmp_path / "eval/src/careflow_eval/example.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n")
    before = source_provenance()
    assert before["git_sha"] == "f" * 40
    assert before["worktree_dirty"] is None
    report = tmp_path / "eval/reports/output.json"
    report.parent.mkdir()
    report.write_text('{"result": "not source"}')
    source.with_suffix(".pyc").write_bytes(b"cache")
    assert source_provenance() == before
    source.write_text("VALUE = 2\n")
    assert source_provenance()["source_sha256"] != before["source_sha256"]


async def test_receipted_local_model_is_verified_before_and_after_inference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = LocalProvider("extraction", "qwen-test", "http://invalid.local/v1")
    chat = AsyncMock(return_value=ChatResult(text=_reply(), model="qwen-test"))
    probe = AsyncMock(return_value={"server_model_ids": ["qwen-test"]})
    monkeypatch.setattr(provider, "chat", chat)
    monkeypatch.setattr("careflow_eval.review.verify_model_endpoint", probe)
    receipt = {"model_id": "qwen-test"}
    cases = _dataset(tmp_path / "cases")
    recording = tmp_path / "recording"
    try:
        result = await run_suite(
            "extraction_en",
            dataset_path=cases,
            provider=provider,
            model_receipt=receipt,
            record_dir=recording,
            include_unreviewed=True,
        )
    finally:
        await provider._client.close()
    assert probe.await_count == 2
    assert chat.await_count == 1
    assert probe.await_args_list[0].args[0].base_url == "http://invalid.local/v1"
    assert probe.await_args_list[0].args[1] == receipt
    assert len(result["metadata"]["model_endpoint_checks"]) == 2
    replay = await run_suite(
        "extraction_en", dataset_path=cases, replay_dir=recording, include_unreviewed=True
    )
    assert replay["metrics"] == result["metrics"]
    assert probe.await_count == 2
    endpoint_receipt = recording / "extraction_en/_model_endpoint.receipt.json"
    verification = json.loads(endpoint_receipt.read_text())
    verification["status"] = "pending"
    endpoint_receipt.write_text(json.dumps(verification))
    with pytest.raises(ReceiptError, match="endpoint verification differs"):
        await run_suite(
            "extraction_en", dataset_path=cases, replay_dir=recording, include_unreviewed=True
        )


@pytest.mark.parametrize("after_inference", [False, True])
async def test_endpoint_mismatch_does_not_return_an_attributed_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after_inference: bool
) -> None:
    provider = LocalProvider("extraction", "qwen-test", "http://invalid.local/v1")
    chat = AsyncMock(return_value=ChatResult(text=_reply(), model="qwen-test"))
    observations = [{"server_model_ids": ["qwen-test"]}] if after_inference else []
    probe = AsyncMock(side_effect=[*observations, ValueError("model identity changed")])
    monkeypatch.setattr(provider, "chat", chat)
    monkeypatch.setattr("careflow_eval.review.verify_model_endpoint", probe)
    cases = _dataset(tmp_path / "cases")
    recording = tmp_path / "recording"
    try:
        with pytest.raises(ValueError, match="model identity changed"):
            await run_suite(
                "extraction_en",
                dataset_path=cases,
                provider=provider,
                record_dir=recording,
                model_receipt={"model_id": "qwen-test"},
                include_unreviewed=True,
            )
    finally:
        await provider._client.close()
    assert chat.await_count == int(after_inference)
    if after_inference:
        with pytest.raises(ReceiptError, match="endpoint verification is incomplete"):
            await run_suite(
                "extraction_en",
                dataset_path=cases,
                replay_dir=recording,
                include_unreviewed=True,
            )
