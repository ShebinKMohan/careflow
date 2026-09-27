"""Receipts bind canonical cassettes and preserve historical, not invented, timings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from careflow_eval.providers import ReceiptError, file_hash
from careflow_eval.runner import run_suite
from careflow_intake.extraction.record import ScriptedChat
from careflow_providers.errors import CassetteMiss


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    path = tmp_path / "cases"
    path.mkdir()
    body = {
        "synthetic": True,
        "id": "synthetic-one",
        "category": "routine",
        "description": "Invented test scenario.",
        "patient": {"age_years": 30, "sex": "female"},
        "turns": [{"question_key": "chief_complaint", "patient_text": "Dry lips"}],
        "expected": {
            "facts": {"chief_complaint": "Dry lips"},
            "alerts": [],
            "level": 5,
            "level_rationale": "Synthetic test label.",
        },
        "generated_by": {"tool": "unit-test", "model": "writer", "date": "2026-09-27"},
    }
    (path / "synthetic-one.yaml").write_text(json.dumps(body))
    return path


REPLY = json.dumps(
    {
        "facts": [
            {"key": "chief_complaint", "value": "Dry lips", "quote": "Dry lips", "turn_id": "t2"}
        ]
    }
)
MODEL_RECEIPT = {"model_id": "synthetic-test-model", "revision": "test-revision"}


async def _record(dataset: Path, directory: Path, **kwargs: Any) -> dict[str, Any]:
    return await run_suite(
        "extraction_en",
        dataset_path=dataset,
        record_dir=directory,
        provider=kwargs.pop("provider", ScriptedChat([REPLY])),
        include_unreviewed=True,
        model_receipt=MODEL_RECEIPT,
        **kwargs,
    )


async def test_replay_preserves_quality_and_measured_latency_without_live_provider(
    dataset: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "recording"
    baseline = await _record(dataset, directory)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("replay cannot construct or configure a live model")

    monkeypatch.setattr("careflow_eval.runner.build_provider", forbidden)
    monkeypatch.setattr("careflow_eval.runner.providers_config", forbidden)
    replay = await run_suite(
        "extraction_en", dataset_path=dataset, replay_dir=directory, include_unreviewed=True
    )
    assert baseline["metrics"] == replay["metrics"]
    original, repeated = baseline["results"][0], replay["results"][0]
    assert original["prediction"] == repeated["prediction"]
    assert original["output_sha256"] == repeated["output_sha256"]
    assert repeated["latency_source"] == "recorded_live"
    assert repeated["replay_wall_ms"] >= 0
    assert replay["metadata"]["model_receipt"] == MODEL_RECEIPT
    assert (
        replay["metadata"]["recording_provenance"]["source_sha256"]
        == (baseline["metadata"]["source_sha256"])
    )
    assert replay["metadata"]["replay_exact"] is True


async def test_rules_record_replay_preserves_latency(tmp_path: Path) -> None:
    baseline = await run_suite("rules", record_dir=tmp_path)
    replay = await run_suite("rules", replay_dir=tmp_path)
    assert baseline["metrics"] == replay["metrics"]
    assert replay["metadata"]["replay_exact"] is True
    assert replay["metadata"]["latency_source"] == ["recorded_rules_execution"]


async def test_dataset_drift_and_model_identity_are_rejected(dataset: Path, tmp_path: Path) -> None:
    directory = tmp_path / "recording"
    await _record(dataset, directory)
    with pytest.raises(ReceiptError, match="model receipt differs"):
        await run_suite(
            "extraction_en",
            dataset_path=dataset,
            replay_dir=directory,
            include_unreviewed=True,
            model_receipt={"model_id": "different"},
        )
    path = dataset / "synthetic-one.yaml"
    body = json.loads(path.read_text())
    body["description"] = "Changed invented scenario."
    path.write_text(json.dumps(body))
    with pytest.raises(ReceiptError, match="dataset_sha256"):
        await run_suite(
            "extraction_en", dataset_path=dataset, replay_dir=directory, include_unreviewed=True
        )


async def test_changed_cassette_fails_hard(dataset: Path, tmp_path: Path) -> None:
    directory = tmp_path / "recording"
    await _record(dataset, directory)
    cassette = directory / "extraction_en/synthetic-one.cassette.json"
    cassette.write_text("{}")
    with pytest.raises(ReceiptError, match="cassette is missing or changed"):
        await run_suite(
            "extraction_en", dataset_path=dataset, replay_dir=directory, include_unreviewed=True
        )


async def test_extractor_cannot_swallow_cassette_miss(dataset: Path, tmp_path: Path) -> None:
    directory = tmp_path / "recording"
    await _record(dataset, directory)
    cassette = directory / "extraction_en/synthetic-one.cassette.json"
    cassette.write_text("{}")
    path = directory / "extraction_en/synthetic-one.receipt.json"
    receipt = json.loads(path.read_text())
    receipt["cassette_sha256"] = file_hash(cassette)
    path.write_text(json.dumps(receipt))
    with pytest.raises(CassetteMiss):
        await run_suite(
            "extraction_en", dataset_path=dataset, replay_dir=directory, include_unreviewed=True
        )


async def test_invalid_model_replies_replay_exact_failure(dataset: Path, tmp_path: Path) -> None:
    directory = tmp_path / "recording"
    baseline = await _record(
        dataset, directory, provider=ScriptedChat(["not json", "still invalid"])
    )
    replay = await run_suite(
        "extraction_en", dataset_path=dataset, replay_dir=directory, include_unreviewed=True
    )
    assert baseline["results"][0]["error_code"] == "invalid_output"
    assert replay["results"][0]["error_code"] == "invalid_output"
    assert replay["metrics"] == baseline["metrics"]
    assert replay["metadata"]["replay_exact"] is True
    assert replay["failed_ids"] == ["synthetic-one"]


@pytest.mark.parametrize("failure", ["timeout", "provider_error"])
async def test_unrecordable_failures_are_explicitly_incomplete(
    dataset: Path, tmp_path: Path, failure: str
) -> None:
    directory = tmp_path / "recording"
    provider = ScriptedChat([REPLY], delay_seconds=1) if failure == "timeout" else ScriptedChat([])
    baseline = await _record(dataset, directory, provider=provider, timeout_seconds=0.001)
    assert baseline["results"][0]["error_code"] == failure
    assert baseline["results"][0]["recording_replayable"] is False
    replay = await run_suite(
        "extraction_en",
        dataset_path=dataset,
        replay_dir=directory,
        include_unreviewed=True,
        timeout_seconds=0.001,
    )
    assert replay["status"] == "incomplete"
    assert replay["metadata"]["replay_exact"] is False
    assert replay["metadata"]["metrics_comparable"] is False
    assert replay["metadata"]["missing_ids"] == ["synthetic-one"]
    assert replay["results"][0]["status"] == "not_run"
    assert "prediction" not in replay["results"][0]
    assert replay["metrics"]["latency_p50_ms"]["value"] is None


async def test_record_never_overwrites_existing_evidence(dataset: Path, tmp_path: Path) -> None:
    directory = tmp_path / "recording"
    await _record(dataset, directory)
    with pytest.raises(ReceiptError, match="recording already exists"):
        await _record(dataset, directory)
