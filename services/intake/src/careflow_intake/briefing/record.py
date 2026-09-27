"""Persona fixtures for the briefing, and recording of the summary model's replies.

    scripts/with_llm.sh uv run python -m careflow_intake.briefing.record --persona chest-pain

A fixture (`tests/fixtures/briefing/<persona>.json`) holds a synthetic `IntakeState` as JSON,
made by `tests/fixtures/briefing/generate.py`. Recording builds the persona's briefing and runs
the configured `summary` provider (the local model) inside the M0-06 `ReplayProvider` in record
mode; the raw replies land in `<persona>.cassette.json`, keyed by the exact messages, and the
outcome (status, drop reasons, sentences) in `<persona>.summary.json`. Tests replay the cassette
without a model and expect the recorded outcome. A changed prompt or item text changes the key:
the replay misses, and the summary is disabled until the persona is recorded again.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from careflow_contracts.generated.alert_schema import Alert
from careflow_contracts.generated.ehr_fetch_schema import EhrStatus
from careflow_contracts.generated.nurse_decision_schema import NurseDecision
from careflow_contracts.generated.structured_facts_schema import EhrContext, Language, Turn
from careflow_contracts.generated.urgency_suggestion_schema import UrgencySuggestion
from careflow_intake.briefing import make_briefing
from careflow_intake.briefing.summary import TASK, Summarizer
from careflow_intake.facts import FactEntry
from careflow_intake.state import ExtractionInfo, Flags, IntakeState
from careflow_intake.wiring import providers_config
from careflow_providers.adapters.replay import Mode, ReplayProvider
from careflow_providers.base import ChatProvider
from careflow_providers.factory import build_provider

FIXTURES: Final = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "briefing"
PERSONAS: Final = (
    "chest-pain",
    "stroke",
    "child-drowsy",
    "sepsis",
    "routine",
    "qr-phone",
    "no-show",
)


def state_to_json(state: IntakeState) -> dict[str, Any]:
    """An IntakeState as JSON (the fields a briefing reads)."""

    def dump(value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json", exclude_unset=True)
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, list):
            return [dump(item) for item in value]
        if isinstance(value, dict):
            return {key: dump(item) for key, item in value.items()}
        return getattr(value, "value", value)

    return {key: dump(value) for key, value in state.items()}


def state_from_json(body: dict[str, Any]) -> IntakeState:
    """The inverse of `state_to_json`."""

    def optional[T](model: type[T], value: Any) -> T | None:
        return None if value is None else model.model_validate(value)  # type: ignore[attr-defined]

    state = IntakeState(
        session_id=body["session_id"],
        clinic_id=body["clinic_id"],
        language=Language(body["language"]),
        arrived_at=datetime.fromisoformat(body["arrived_at"]),
    )
    state["patient_ref"] = body.get("patient_ref")
    if (status := body.get("ehr_status")) is not None:
        state["ehr_status"] = EhrStatus(status)
    state["ehr_errors"] = list(body.get("ehr_errors", []))
    state["ehr_context"] = optional(EhrContext, body.get("ehr_context"))
    state["turns"] = [Turn.model_validate(turn) for turn in body.get("turns", [])]
    state["facts"] = [FactEntry.model_validate(entry) for entry in body.get("facts", [])]
    state["alerts"] = [Alert.model_validate(alert) for alert in body.get("alerts", [])]
    state["ruleset_version"] = body.get("ruleset_version")
    state["extraction"] = ExtractionInfo(**body.get("extraction", {}))
    state["suggestion"] = optional(UrgencySuggestion, body.get("suggestion"))
    state["suggestion_unavailable"] = body.get("suggestion_unavailable")
    state["nurse_decision"] = optional(NurseDecision, body.get("nurse_decision"))
    state["flags"] = Flags(**body.get("flags", {}))
    return state


def load_persona(name: str, fixtures: Path = FIXTURES) -> IntakeState:
    body = json.loads((fixtures / f"{name}.json").read_text(encoding="utf-8"))
    return state_from_json(body["state"])


def cassette(name: str, fixtures: Path = FIXTURES) -> Path:
    return fixtures / f"{name}.cassette.json"


def expected(name: str, fixtures: Path = FIXTURES) -> Path:
    """What the recording produced: the status, the drop reasons and the sentences."""
    return fixtures / f"{name}.summary.json"


def replay_summarizer(
    name: str, *, mode: Mode = "replay", inner: ChatProvider | None = None
) -> Summarizer:
    """A Summarizer answering from (or recording into) the persona's cassette."""
    providers = providers_config()
    model = providers.tasks[TASK].model_dump().get("model", TASK)

    def provider() -> ChatProvider:
        wrapped = inner
        if wrapped is None and mode != "replay":
            wrapped = build_provider(TASK, providers)
        return ReplayProvider(
            wrapped, task=TASK, model=str(model), cassette=cassette(name), mode=mode
        )

    return Summarizer(provider=provider, timeout_seconds=300.0 if mode == "record" else 30.0)


async def record(name: str) -> dict[str, Any]:
    state = load_persona(name)
    decision = state.get("nurse_decision")
    if decision is None:
        raise ValueError(f"{name}: the fixture has no nurse decision")
    summarizer = replay_summarizer(name, mode="record")
    result = await make_briefing(state, now=decision.decided_at, summarizer=summarizer)
    briefing = result.briefing
    outcome = {
        "persona": name,
        "summary_status": briefing.summary_status.value,
        "reasons": result.summary_reasons,
        "sentences": [s.text for s in briefing.summary.sentences] if briefing.summary else [],
    }
    text = json.dumps(outcome, indent=2, ensure_ascii=False) + "\n"
    expected(name).write_text(text, encoding="utf-8")
    return outcome


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--persona", choices=[*PERSONAS, "all"], required=True)
    args = parser.parse_args(argv)
    names = PERSONAS if args.persona == "all" else (args.persona,)
    for name in names:
        print(json.dumps(asyncio.run(record(name)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "FIXTURES",
    "PERSONAS",
    "cassette",
    "expected",
    "load_persona",
    "replay_summarizer",
    "state_from_json",
    "state_to_json",
]
