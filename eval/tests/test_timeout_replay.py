"""Negative integrity checks for replaying the one confirmed live timeout event.

Each test copies the real `child-parent-unsure-waking` recording (receipt, partial cassette and
endpoint completion manifest), changes one property, and replays it through the production
extractor. A failure the recording does not prove stays `not_run`; evidence that was changed
after the recording fails hard.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from careflow_eval.datasets import load_dataset
from careflow_eval.models import Scenario
from careflow_eval.providers import ReceiptError, file_hash
from careflow_eval.runner import DEFAULT_DATASET, REPO
from careflow_eval.suites import extraction_en
from careflow_intake.extraction.extractor import DEFAULT_TIMEOUT_SECONDS

CASE = "child-parent-unsure-waking"
RECORDED = REPO / "eval" / "replays" / "extraction_en"
MANIFEST = "_model_endpoint.receipt.json"


@pytest.fixture(scope="module")
def scenario() -> Scenario:
    return next(s for s in load_dataset(DEFAULT_DATASET).scenarios if s.id == CASE)


@pytest.fixture
def recording(tmp_path: Path) -> Path:
    directory = tmp_path / "extraction_en"
    directory.mkdir()
    for name in (f"{CASE}.receipt.json", f"{CASE}.cassette.json", MANIFEST):
        shutil.copy2(RECORDED / name, directory / name)
    return directory


def _receipt(directory: Path) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((directory / f"{CASE}.receipt.json").read_text())
    return body


def _write(directory: Path, receipt: dict[str, Any], *, rebind: bool = True) -> None:
    """Save a changed receipt; `rebind` re-signs the completion manifest so that only the
    changed property is under test."""
    path = directory / f"{CASE}.receipt.json"
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    if rebind:
        manifest = directory / MANIFEST
        body = json.loads(manifest.read_text())
        body["case_receipts"][CASE] = file_hash(path)
        manifest.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")


async def _replay(directory: Path, scenario: Scenario) -> dict[str, Any]:
    receipt = _receipt(directory)
    return await extraction_en.run_case(
        scenario,
        bindings=receipt["bindings"],
        provider=None,
        provider_kind="replay",
        record_dir=None,
        replay_dir=directory,
        timeout_seconds=receipt["bindings"]["timeout_seconds"],
        model_receipt=receipt["model_receipt"],
        source_provenance={},
        model_endpoint_verification_required=False,
    )


async def test_the_retained_recording_replays_its_timeout_through_the_extractor(
    recording: Path, scenario: Scenario
) -> None:
    receipt = _receipt(recording)
    assert receipt["replayable"] is False  # the historical flag is never rewritten
    assert [call["status"] for call in receipt["calls"]] == ["ok", "failed"]
    row = await _replay(recording, scenario)
    assert row["status"] == "completed"
    assert row["replay_method"] == "confirmed_timeout_event"
    assert row["failure_event_replayed"] is True
    assert row["recording_replayable"] is False
    assert row["error_code"] == "timeout"
    assert row["prediction"]["extraction_incomplete"] is True
    assert row["prediction"]["facts"] == []
    assert row["prediction"]["latency_ms"] == receipt["duration_ms"]
    assert row["output_sha256"] == receipt["output_sha256"]
    assert row["rules_validation"] is False


def _set(path: str, value: Any) -> Callable[[dict[str, Any]], None]:
    def change(receipt: dict[str, Any]) -> None:
        *parents, last = path.split(".")
        target: Any = receipt
        for part in parents:
            target = target[int(part)] if part.isdigit() else target[part]
        target[int(last) if last.isdigit() else last] = value

    return change


def _swap_call_order(receipt: dict[str, Any]) -> None:
    receipt["calls"].reverse()


def _third_call(receipt: dict[str, Any]) -> None:
    receipt["calls"].insert(0, {**receipt["calls"][0], "request_sha256": "a" * 64})


def _repeated_request(receipt: dict[str, Any]) -> None:
    receipt["calls"][1]["request_sha256"] = receipt["calls"][0]["request_sha256"]


UNPROVEN = {
    "unknown error type": _set("calls.1.error_type", "ReadTimeout"),
    "provider error code": _set("failure_code", "provider_error"),
    "non-production timeout bound": _set("bindings.timeout_seconds", 30.0),
    "shorter than the deadline": _set("duration_ms", DEFAULT_TIMEOUT_SECONDS * 1000 - 1),
    "calls longer than the case": _set("calls.0.wall_ms", 15_000.0),
    "zero-length final call": _set("calls.1.wall_ms", 0.0),
    "negative call time": _set("calls.0.wall_ms", -1.0),
    "failure before a reply": _swap_call_order,
    "three calls": _third_call,
    "repeated request": _repeated_request,
    "malformed request hash": _set("calls.0.request_sha256", "not-a-sha256"),
    "missing call timing": _set("calls.1.wall_ms", None),
}


@pytest.mark.parametrize("change", UNPROVEN.values(), ids=UNPROVEN.keys())
async def test_unproven_failure_events_stay_not_run(
    recording: Path, scenario: Scenario, change: Callable[[dict[str, Any]], None]
) -> None:
    receipt = _receipt(recording)
    change(receipt)
    _write(recording, receipt)
    row = await _replay(recording, scenario)
    assert row["status"] == "not_run"
    assert "has no replayable reply" in row["reason"]
    assert row["replay_exact"] is False
    assert "prediction" not in row


async def test_a_reply_only_claim_cannot_skip_the_failed_call(
    recording: Path, scenario: Scenario
) -> None:
    receipt = _receipt(recording)
    receipt["replayable"] = True
    _write(recording, receipt)
    with pytest.raises(ReceiptError, match="unsupported failure event"):
        await _replay(recording, scenario)


async def test_a_changed_partial_cassette_fails_hard(recording: Path, scenario: Scenario) -> None:
    cassette = recording / f"{CASE}.cassette.json"
    body = json.loads(cassette.read_text())
    for entry in body.values():
        entry["text"] += " "
    cassette.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")
    with pytest.raises(ReceiptError, match="cassette is missing or changed"):
        await _replay(recording, scenario)


async def test_a_missing_partial_cassette_fails_hard(recording: Path, scenario: Scenario) -> None:
    (recording / f"{CASE}.cassette.json").unlink()
    with pytest.raises(ReceiptError, match="cassette is missing or changed"):
        await _replay(recording, scenario)


async def test_a_first_request_timeout_cannot_carry_an_unbound_cassette(
    recording: Path, scenario: Scenario
) -> None:
    receipt = _receipt(recording)
    receipt["calls"] = receipt["calls"][1:]
    receipt["cassette_sha256"] = None
    _write(recording, receipt)
    with pytest.raises(ReceiptError, match="unbound cassette"):
        await _replay(recording, scenario)


async def test_a_receipt_changed_after_the_endpoint_manifest_fails_hard(
    recording: Path, scenario: Scenario
) -> None:
    receipt = _receipt(recording)
    receipt["duration_ms"] += 1
    _write(recording, receipt, rebind=False)
    with pytest.raises(ReceiptError, match="endpoint verification differs"):
        await _replay(recording, scenario)


async def test_a_missing_endpoint_manifest_fails_hard(recording: Path, scenario: Scenario) -> None:
    (recording / MANIFEST).unlink()
    with pytest.raises(ReceiptError, match="endpoint verification is incomplete"):
        await _replay(recording, scenario)


async def test_a_different_model_identity_fails_hard(recording: Path, scenario: Scenario) -> None:
    receipt = _receipt(recording)
    receipt["model_receipt"]["revision"] = "0" * 40
    _write(recording, receipt)
    with pytest.raises(ReceiptError, match="endpoint verification differs"):
        await _replay(recording, scenario)


@pytest.mark.parametrize("index", [0, 1], ids=["replied request", "timed-out request"])
async def test_an_out_of_order_request_fails_hard_and_is_not_swallowed(
    recording: Path, scenario: Scenario, index: int
) -> None:
    receipt = _receipt(recording)
    receipt["calls"][index]["request_sha256"] = f"{index + 1}" * 64
    _write(recording, receipt)
    # The extractor turns provider exceptions into `provider_error`; the harness re-raises.
    with pytest.raises(ReceiptError, match="does not match the recorded call sequence"):
        await _replay(recording, scenario)


async def test_a_different_output_fails_hard(recording: Path, scenario: Scenario) -> None:
    receipt = _receipt(recording)
    receipt["output_sha256"] = "f" * 64
    _write(recording, receipt)
    with pytest.raises(ReceiptError, match="output differs from the recording"):
        await _replay(recording, scenario)
