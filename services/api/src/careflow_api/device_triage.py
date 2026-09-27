"""On-device urgency suggestions (M3-02): the kiosk runs Laya; the server decides what it means.

- `model_input`: the session's structured facts as the model reads them (`facts_to_state`,
  input contract `laya-state/v1`, built only here: the kiosk has no copy of the serializer) and
  the SHA-256 of that state. Never a level, an alert or a suggestion.
- `derive`: the kiosk is an untrusted client. Its model version must be `released` in the
  registry, its probabilities must sum to 1 (within 1e-3), and the input it read must still be the
  session's (else 409). The level (argmax), the confidence and the reason texts
  (`reasons_from_keys`) are derived here; only the probabilities and reason keys come from it.
- `record`: every post is kept in `device_suggestions` (applied or not) and, when it became the
  session's suggestion, `triage.suggested` goes out (key `{session_id}:triage.suggested:laya`).
- `suggestion_source`: the nurse's badge, e.g. "Laya 1.0.0 · kiosk" or "HGB 1.0.0 · server"; the
  server model's family and version come from the registry.

Rules 1 and 3 are untouched: this never skips `run_rules` or `nurse_review`, and the nurse still
confirms every level.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from functools import cache
from typing import Any, Final

from careflow_api.events.outbox import OutboxPublisher
from careflow_api.events.publisher import Event
from careflow_api.models import DeviceSuggestionRow
from careflow_api.rls import clinic_session
from careflow_contracts import StructuredFacts
from careflow_contracts.api_models import DeviceUrgencySuggestion, ModelInput
from careflow_contracts.events import EventType, TriageSuggestedData
from careflow_contracts.generated.urgency_suggestion_schema import TriageScale, UrgencySuggestion
from careflow_triage import registry
from careflow_triage.laya_ktas.serialize import STATE_VERSION, facts_to_state, reasons_from_keys
from careflow_triage.scales import display_label

LAYA_MODEL_ID: Final = "laya-ktas"
PROBABILITY_TOLERANCE: Final = 1e-3
NO_REASONS: Final = "The kiosk model sent no reasons"


class StaleInput(ValueError):
    """The facts changed since the kiosk read the model input (409)."""


class Unacceptable(ValueError):
    """Not a released model version, or probabilities that are not a distribution (422)."""


def state_sha256(state: dict[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(state, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def model_input(facts: StructuredFacts) -> ModelInput:
    state = facts_to_state(facts).state
    return ModelInput.model_validate(
        {"input_contract": STATE_VERSION, "state": state, "facts_sha256": state_sha256(state)}
    )


@cache
def _registry() -> dict[str, Any]:
    return registry.load()


def released(family: str, version: str) -> bool:
    record = registry.entry(_registry(), family, version)
    return record is not None and record["status"] == "released"


def derive(
    body: DeviceUrgencySuggestion,
    facts: StructuredFacts,
    *,
    scale: TriageScale,
    now: dt.datetime,
) -> UrgencySuggestion:
    family, version = body.model_family.value, body.model_version
    if not released(family, version):
        raise Unacceptable(f"{family} {version} is not a released model version.")
    probabilities = [float(p) for p in body.probabilities]
    if abs(sum(probabilities) - 1.0) > PROBABILITY_TOLERANCE:
        raise Unacceptable("The probabilities must sum to 1.")
    laya_input = facts_to_state(facts)
    if body.facts_sha256 != state_sha256(laya_input.state):
        raise StaleInput("The facts changed since the model input was read.")
    best = max(range(len(probabilities)), key=probabilities.__getitem__)
    level = best + 1
    keys = [key.value for key in body.reason_keys]
    reasons = [
        {"text": reason.text, "fact_ids": reason.fact_ids}
        for reason in reasons_from_keys(keys, laya_input)
    ] or [{"text": NO_REASONS, "fact_ids": []}]
    digest = hashlib.sha256(
        f"{facts.session_id}|{family}|{version}|{body.facts_sha256}|{probabilities}".encode()
    ).hexdigest()
    return UrgencySuggestion.model_validate(
        {
            "suggestion_id": f"sug_{digest[:24]}",
            "session_id": facts.session_id,
            "level": level,
            "scale": scale.value,
            "display_label": display_label(level, scale.value),
            "confidence": probabilities[best],
            "reasons": reasons,
            "model_id": LAYA_MODEL_ID,
            "model_version": version,
            "created_at": now.isoformat(),
        }
    )


def kiosk_source(body: DeviceUrgencySuggestion) -> dict[str, str]:
    return {"family": body.model_family.value, "version": body.model_version, "device": "kiosk"}


def server_source(suggestion: UrgencySuggestion) -> dict[str, str]:
    """The registry's family and version for the server model that made `suggestion`."""
    for record in _registry()["entries"]:
        if record["artifact"].rstrip("/").endswith(suggestion.model_version):
            return {"family": record["family"], "version": record["version"], "device": "server"}
    return {"family": suggestion.model_id, "version": suggestion.model_version, "device": "server"}


def suggestion_source(state: dict[str, Any]) -> dict[str, str] | None:
    suggestion = state.get("suggestion")
    if suggestion is None:
        return None
    return state.get("suggestion_source") or server_source(suggestion)


async def record(
    clinic_id: uuid.UUID,
    body: DeviceUrgencySuggestion,
    suggestion: UrgencySuggestion,
    *,
    applied: bool,
    events: OutboxPublisher,
    now: dt.datetime,
) -> None:
    async with clinic_session(clinic_id) as db:
        db.add(
            DeviceSuggestionRow(
                clinic_id=clinic_id,
                session_id=uuid.UUID(suggestion.session_id),
                suggestion_id=suggestion.suggestion_id,
                model_family=body.model_family.value,
                model_version=body.model_version,
                facts_sha256=body.facts_sha256,
                probabilities=[float(p) for p in body.probabilities],
                level=int(suggestion.level),
                confidence=float(suggestion.confidence),
                reason_keys=[key.value for key in body.reason_keys],
                latency_ms=int(body.latency_ms),
                applied=applied,
            )
        )
        if applied:
            data = TriageSuggestedData(
                clinic_id=str(clinic_id),
                session_id=suggestion.session_id,
                suggestion_id=suggestion.suggestion_id,
                level=suggestion.level,
                scale=suggestion.scale,
                confidence=suggestion.confidence,
                model_id=suggestion.model_id,
                model_version=suggestion.model_version,
                created_at=now,
            )
            key = f"{suggestion.session_id}:triage.suggested:laya"
            await events.add(db, Event(EventType.triage_suggested, data, now, idempotency_key=key))


__all__ = [
    "LAYA_MODEL_ID",
    "StaleInput",
    "Unacceptable",
    "derive",
    "kiosk_source",
    "model_input",
    "record",
    "released",
    "server_source",
    "state_sha256",
    "suggestion_source",
]
