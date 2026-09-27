"""Per-case canonical provider cassettes, with receipts proving what can be replayed."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from careflow_intake.extraction.extractor import DEFAULT_TIMEOUT_SECONDS
from careflow_providers.adapters.replay import ReplayProvider, cassette_key
from careflow_providers.base import ChatProvider, ChatResult, Message
from careflow_providers.errors import CassetteMiss

RECEIPT_VERSION = 1


class ReceiptError(ValueError):
    """The recording does not prove this dataset, configuration, or result."""


class IncompleteRecording(ReceiptError):
    """Neither a recorded reply nor a proven supported failure event can reproduce this case."""


def canonical_hash(value: Any) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode()).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def case_paths(directory: Path, case_id: str) -> tuple[Path, Path]:
    if not case_id or Path(case_id).name != case_id or case_id in {".", ".."}:
        raise ValueError("case id must be a filename, without path components")
    return directory / f"{case_id}.cassette.json", directory / f"{case_id}.receipt.json"


def _finite_nonnegative(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and math.isfinite(value)
        and value >= 0
    )


def _confirmed_timeout(receipt: Mapping[str, Any]) -> bool:
    """Recognize the production extractor's finalized deadline event, never bare cancellation.

    The original reply-only replayable flag remains unchanged. A caller must still verify
    bindings, cassette/endpoint hashes, exact request sequence and resulting output hash.
    """
    calls = receipt.get("calls")
    if (
        receipt.get("replayable") is not False
        or receipt.get("failure_code") != "timeout"
        or receipt.get("bindings", {}).get("timeout_seconds") != DEFAULT_TIMEOUT_SECONDS
        or not isinstance(calls, list)
        or not 1 <= len(calls) <= 2
        or not all(isinstance(call, dict) for call in calls)
    ):
        return False
    if any(call.get("status") != "ok" for call in calls[:-1]):
        return False
    if calls[-1].get("status") != "failed" or calls[-1].get("error_type") != "CancelledError":
        return False
    if any(not _finite_nonnegative(call.get("wall_ms")) for call in calls):
        return False
    if any(
        not isinstance(call.get("request_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", call["request_sha256"]) is None
        for call in calls
    ) or len({call["request_sha256"] for call in calls}) != len(calls):
        return False
    duration = receipt.get("duration_ms")
    if (
        not isinstance(duration, int | float)
        or not _finite_nonnegative(duration)
        or duration < DEFAULT_TIMEOUT_SECONDS * 1000
        or sum(call["wall_ms"] for call in calls) > duration
        or calls[-1]["wall_ms"] <= 0
    ):
        return False
    if len(calls) > 1 and not receipt.get("cassette_sha256"):
        return False
    if len(calls) == 1 and receipt.get("cassette_sha256") is not None:
        return False
    return True


class AuditedProvider:
    """Observe the existing ReplayProvider without inventing another reply mechanism.

    LlmExtractor intentionally catches provider errors. Keep cassette misses separately so the
    harness can re-raise them after extraction instead of scoring an empty fabricated result.
    """

    def __init__(
        self,
        provider: ReplayProvider,
        *,
        replay_calls: Sequence[Mapping[str, Any]] | None = None,
        confirmed_timeout: bool = False,
    ) -> None:
        self.provider = provider
        self.task, self.model_name = provider.task, provider.model_name
        self.calls: list[dict[str, Any]] = []
        self.miss: CassetteMiss | None = None
        self.integrity_error: ReceiptError | None = None
        self._replay_calls = replay_calls
        self._confirmed_timeout = confirmed_timeout
        self.failure_event_replayed = False

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        call: dict[str, Any] = {
            "request_sha256": cassette_key(self.task, self.model_name, messages, temperature),
        }
        started = time.perf_counter()
        try:
            if self._replay_calls is not None:
                index = len(self.calls)
                if (
                    index >= len(self._replay_calls)
                    or self._replay_calls[index]["request_sha256"] != call["request_sha256"]
                ):
                    raise ReceiptError("replay request does not match the recorded call sequence")
                expected = self._replay_calls[index]
                if expected["status"] == "failed":
                    if not self._confirmed_timeout or index != len(self._replay_calls) - 1:
                        raise ReceiptError("replay encountered an unsupported failure event")
                    self.failure_event_replayed = True
                    call["failure_event_replayed"] = True
                    # This reproduces the confirmed failure event, not an absent model reply.
                    raise TimeoutError("replayed extractor-confirmed timeout event")
            result = await self.provider.chat(
                messages, temperature=temperature, max_tokens=max_tokens
            )
        except BaseException as exc:
            call.update(status="failed", error_type=type(exc).__name__)
            if isinstance(exc, CassetteMiss):
                self.miss = exc
            if isinstance(exc, ReceiptError):
                self.integrity_error = exc
            raise
        else:
            call.update(status="ok", response_model=result.model)
            return result
        finally:
            call["wall_ms"] = (time.perf_counter() - started) * 1000
            self.calls.append(call)

    async def stream(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        yield (await self.chat(messages)).text

    def require_no_miss(self) -> None:
        if self.integrity_error is not None:
            raise self.integrity_error
        if self.miss is not None:
            raise self.miss


def read_receipt(
    directory: Path, case_id: str, bindings: Mapping[str, Any], *, require_replayable: bool = True
) -> dict[str, Any]:
    cassette, path = case_paths(directory, case_id)
    if not path.exists():
        raise ReceiptError(f"{case_id}: missing recording receipt")
    body: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if body.get("schema_version") != RECEIPT_VERSION or body.get("case_id") != case_id:
        raise ReceiptError(f"{case_id}: unsupported or mismatched recording receipt")
    for key, value in bindings.items():
        if body.get("bindings", {}).get(key) != value:
            raise ReceiptError(f"{case_id}: recording binding differs: {key}")
    duration = body.get("duration_ms")
    if isinstance(duration, bool) or not isinstance(duration, int | float):
        raise ReceiptError(f"{case_id}: missing measured live duration")
    if not math.isfinite(duration) or duration < 0:
        raise ReceiptError(f"{case_id}: invalid measured live duration")
    if body.get("model_endpoint_verification_required"):
        verification_path = directory / "_model_endpoint.receipt.json"
        if not verification_path.is_file():
            raise ReceiptError(f"{case_id}: recorded model endpoint verification is incomplete")
        verification = json.loads(verification_path.read_text(encoding="utf-8"))
        if (
            verification.get("schema_version") != RECEIPT_VERSION
            or verification.get("status") != "verified"
            or verification.get("model_receipt_sha256") != canonical_hash(body["model_receipt"])
            or verification.get("case_receipts", {}).get(case_id) != file_hash(path)
        ):
            raise ReceiptError(f"{case_id}: recorded model endpoint verification differs")
    if body.get("cassette_sha256") is not None:
        if not cassette.is_file() or file_hash(cassette) != body["cassette_sha256"]:
            raise ReceiptError(f"{case_id}: recording cassette is missing or changed")
    elif cassette.exists():
        raise ReceiptError(f"{case_id}: recording has an unbound cassette")
    if not body.get("replayable") and not _confirmed_timeout(body) and require_replayable:
        raise IncompleteRecording(
            f"{case_id}: live {body.get('failure_code') or 'provider failure'} "
            "has no replayable reply"
        )
    if body.get("replayable") and body.get("cassette_sha256") is None:
        raise ReceiptError(f"{case_id}: recording cassette is missing or changed")
    return body


def recorded_provider(directory: Path, case_id: str, provider: ChatProvider) -> AuditedProvider:
    cassette, receipt = case_paths(directory, case_id)
    if cassette.exists() or receipt.exists():
        raise ReceiptError(
            f"{case_id}: recording already exists; choose a fresh recording directory"
        )
    return AuditedProvider(
        ReplayProvider(
            provider,
            task=provider.task,
            model=provider.model_name,
            cassette=cassette,
            mode="record",
        )
    )


def replayed_provider(directory: Path, case_id: str, receipt: Mapping[str, Any]) -> AuditedProvider:
    cassette, _ = case_paths(directory, case_id)
    return AuditedProvider(
        ReplayProvider(
            None,
            task="extraction",
            model=receipt["logical_model"],
            cassette=cassette,
            mode="replay",
        ),
        replay_calls=receipt["calls"],
        confirmed_timeout=_confirmed_timeout(receipt),
    )


def write_receipt(
    directory: Path,
    case_id: str,
    *,
    bindings: Mapping[str, Any],
    provider: AuditedProvider,
    output: Mapping[str, Any],
    duration_ms: float,
    failure_code: str | None,
    provider_kind: str,
    model_receipt: Mapping[str, Any] | None,
    source_provenance: Mapping[str, Any],
    model_endpoint_verification_required: bool,
) -> dict[str, Any]:
    cassette, path = case_paths(directory, case_id)
    replayable = bool(provider.calls) and all(call["status"] == "ok" for call in provider.calls)
    body = {
        "schema_version": RECEIPT_VERSION,
        "case_id": case_id,
        "bindings": dict(bindings),
        "provider_kind": provider_kind,
        "logical_model": provider.model_name,
        "response_models": sorted(
            {call["response_model"] for call in provider.calls if call["status"] == "ok"}
        ),
        "model_receipt": dict(model_receipt) if model_receipt is not None else None,
        "source_provenance": dict(source_provenance),
        "model_endpoint_verification_required": model_endpoint_verification_required,
        "calls": provider.calls,
        "duration_ms": duration_ms,
        "output_sha256": canonical_hash(output),
        "cassette_sha256": file_hash(cassette) if cassette.exists() else None,
        "replayable": replayable,
        "failure_code": failure_code,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    return body


def complete_endpoint_recording(
    directory: Path,
    case_ids: Sequence[str],
    *,
    model_receipt: Mapping[str, Any],
    observations: Sequence[Mapping[str, Any]],
) -> None:
    """Only publish endpoint attribution after both live probes have succeeded."""
    if len(observations) != 2:
        raise ReceiptError("recorded model endpoint needs before/after verification")
    body = {
        "schema_version": RECEIPT_VERSION,
        "status": "verified",
        "model_receipt_sha256": canonical_hash(dict(model_receipt)),
        "observations": list(observations),
        "case_receipts": {
            case_id: file_hash(case_paths(directory, case_id)[1]) for case_id in case_ids
        },
    }
    (directory / "_model_endpoint.receipt.json").write_text(
        json.dumps(body, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def verify_replayed_output(
    case_id: str, receipt: Mapping[str, Any], provider: AuditedProvider, output: Mapping[str, Any]
) -> None:
    provider.require_no_miss()
    observed = [call["request_sha256"] for call in provider.calls]
    expected = [call["request_sha256"] for call in receipt["calls"]]
    if observed != expected:
        raise ReceiptError(f"{case_id}: replay made different provider requests")
    if [call["status"] for call in provider.calls] != [call["status"] for call in receipt["calls"]]:
        raise ReceiptError(f"{case_id}: replay call outcomes differ from recording")
    if _confirmed_timeout(receipt) and not provider.failure_event_replayed:
        raise ReceiptError(f"{case_id}: confirmed timeout event was not reproduced")
    if canonical_hash(output) != receipt["output_sha256"]:
        raise ReceiptError(
            f"{case_id}: replayed extraction or rules output differs from the recording"
        )


__all__ = [
    "AuditedProvider",
    "IncompleteRecording",
    "ReceiptError",
    "canonical_hash",
    "case_paths",
    "complete_endpoint_recording",
    "file_hash",
    "read_receipt",
    "recorded_provider",
    "replayed_provider",
    "verify_replayed_output",
    "write_receipt",
]
