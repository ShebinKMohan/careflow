"""Extract patient facts with the production extractor, then evaluate rules on its actual output."""

from __future__ import annotations

import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from careflow_contracts import ExtractionStatus, Language
from careflow_eval.datasets import ARRIVED_AT, NOW, context_entries, transcript
from careflow_eval.models import Scenario
from careflow_eval.providers import (
    IncompleteRecording,
    read_receipt,
    recorded_provider,
    replayed_provider,
    verify_replayed_output,
    write_receipt,
)
from careflow_intake.extraction.extractor import LlmExtractor
from careflow_intake.facts import to_structured_facts
from careflow_providers.base import ChatProvider
from careflow_rules import evaluate


def expected_slots(scenario: Scenario) -> dict[str, Any]:
    """Gold enters scoring only. A symptoms list is one scalar slot per symptom."""
    slots = []
    for key, value in scenario.expected.facts.items():
        values = value if isinstance(value, list) else [value]
        slots.extend({"key": key.value, "value": item} for item in values)
    return {"facts": slots, "alerts": sorted(rule.value for rule in scenario.expected.alerts)}


async def run_case(
    scenario: Scenario,
    *,
    bindings: Mapping[str, Any],
    provider: ChatProvider | None,
    provider_kind: str,
    record_dir: Path | None,
    replay_dir: Path | None,
    timeout_seconds: float,
    model_receipt: Mapping[str, Any] | None,
    source_provenance: Mapping[str, Any],
    model_endpoint_verification_required: bool,
) -> dict[str, Any]:
    if scenario.language != Language.en:
        raise ValueError(f"{scenario.id}: extraction_en only accepts English scenarios")
    receipt = None
    if replay_dir is not None:
        try:
            receipt = read_receipt(replay_dir, scenario.id, bindings)
        except IncompleteRecording as exc:
            return {
                "id": scenario.id,
                "status": "not_run",
                "reason": str(exc),
                "expected": expected_slots(scenario),
                "replay_exact": False,
            }
        selected = replayed_provider(replay_dir, scenario.id, receipt)
        provider_kind = receipt["provider_kind"]
    elif record_dir is not None:
        if provider is None:
            raise ValueError("record mode needs an extraction provider")
        selected = recorded_provider(record_dir, scenario.id, provider)
    else:
        selected = None
    actual_provider = selected or provider
    if actual_provider is None:
        raise ValueError("live extraction needs an extraction provider")
    turns, question_keys = transcript(scenario)
    extractor = LlmExtractor(
        actual_provider, timeout_seconds=timeout_seconds, provider_kind=provider_kind
    )
    started = time.perf_counter()
    # Never pass gold, context facts, or expected urgency to the language model.
    extracted = await extractor.extract(turns, [], question_keys, scenario.language)
    measured_ms = (time.perf_counter() - started) * 1000
    if selected is not None:
        selected.require_no_miss()
    facts = to_structured_facts(
        session_id=f"ses_eval_{scenario.id}",
        clinic_id="clinic_eval",
        language=scenario.language,
        entries=[*context_entries(scenario), *extracted.facts],
        extraction_status=ExtractionStatus.incomplete
        if extracted.extraction_incomplete
        else ExtractionStatus.complete,
    )
    outcome = evaluate(facts, NOW, arrived_at=ARRIVED_AT)
    output = {
        "extraction": extracted.model_dump(mode="json"),
        "rules": outcome.model_dump(mode="json"),
    }
    if record_dir is not None and selected is not None:
        receipt = write_receipt(
            record_dir,
            scenario.id,
            bindings=bindings,
            provider=selected,
            output=output,
            duration_ms=measured_ms,
            failure_code=extracted.error_code,
            provider_kind=provider_kind,
            model_receipt=model_receipt,
            source_provenance=source_provenance,
            model_endpoint_verification_required=model_endpoint_verification_required,
        )
    if replay_dir is not None and selected is not None and receipt is not None:
        verify_replayed_output(scenario.id, receipt, selected, output)
    expected = expected_slots(scenario)
    alerts = sorted(alert.rule_id.value for alert in outcome.alerts)
    result: dict[str, Any] = {
        "id": scenario.id,
        "category": scenario.category,
        "status": "completed",
        "prediction": {
            "facts": [
                {"key": entry.key.value, "value": entry.base.model_dump(mode="json")["value"]}
                for entry in extracted.facts
            ],
            "alerts": alerts,
            "extraction_incomplete": extracted.extraction_incomplete,
            "dropped_count": extracted.dropped_count,
            "latency_ms": receipt["duration_ms"]
            if replay_dir is not None and receipt is not None
            else measured_ms,
        },
        "expected": expected,
        "rules_validation": alerts == expected["alerts"],
        "error_code": extracted.error_code,
        "output_sha256": receipt["output_sha256"] if receipt is not None else None,
        "latency_source": "recorded_live" if replay_dir is not None else "measured_live",
        "replay_exact": True if replay_dir is not None else None,
        "recording_replayable": receipt["replayable"] if receipt is not None else None,
    }
    if replay_dir is not None:
        result["replay_wall_ms"] = measured_ms
    return result
