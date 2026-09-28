"""`build_ports`: the intake graph's ports in the API (M1-12).

It extends `careflow_intake.wiring.build_real_ports` (M1-07: the LLM extractor, the rules engine
and the checklist conversation) with the API's own adapters: check-in identity, the FHIR EHR
reader (M1-08), the urgency model (M1-04, from TRIAGE_MODEL_PATH, or no suggestion), the queue
(M1-10), the write-back (M1-09), the briefing (M1-16), the outbox events (M1-11), and the side
questions asked mid-intake (M2-10): the turn classifier, the ask-the-clinic agent and the nurse
flags. The choice for every port is logged at startup and shown on the admin status page.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from careflow_api.ask import AskService
from careflow_api.briefing.service import BriefingService
from careflow_api.intake.ports import (
    CachedEhrReader,
    CheckinIdentifier,
    NoTriageModel,
    OutboxEvents,
    QueuePort,
    TriageUrgency,
    WritebackPort,
)
from careflow_api.intake.side_questions import AgentClinicQA, SqlNurseFlags, TurnClassifier
from careflow_api.queue.service import QueueService
from careflow_intake.ports import IntakePorts
from careflow_intake.wiring import build_real_ports, load_intake_config, providers_config
from careflow_providers.adapters.replay import provider_mode
from careflow_providers.factory import build_provider
from careflow_rules import RULESET_VERSION

logger = logging.getLogger("careflow_api.intake")

REPO: Final = Path(__file__).resolve().parents[5]
ARTIFACTS: Final = REPO / "services" / "triage" / "artifacts"


def triage_model_path() -> Path:
    """Explicit artifact path, or the newest artifact of the selected model kind.

    HGB remains the default for installations without the optional Laya dependency. Selecting
    Laya never falls back to an HGB artifact when its checkpoint is missing.
    """
    if configured := os.environ.get("TRIAGE_MODEL_PATH"):
        return Path(configured)
    kind = os.environ.get("TRIAGE_MODEL_KIND", "hgb")
    if kind == "laya":
        found = [p for p in (ARTIFACTS / "laya").glob("*") if (p / "model.safetensors").is_file()]
    elif kind == "hgb":
        found = [p for p in ARTIFACTS.glob("*hgb*") if (p / "model.joblib").is_file()]
    else:
        return ARTIFACTS / "none"
    if not found:
        return ARTIFACTS / "none"
    found.sort(key=lambda p: (not p.name.startswith("fixture-"), p.stat().st_mtime))
    return found[-1]


@dataclass(frozen=True)
class PortChoice:
    port: str
    implementation: str
    detail: str = ""
    degraded: bool = False


@dataclass(frozen=True)
class Wiring:
    ports: IntakePorts
    choices: tuple[PortChoice, ...]
    urgency_model_version: str


def build_ports(*, queue: QueueService, briefing: BriefingService, ask: AskService) -> Wiring:
    """The real ports; the urgency model falls back to "no suggestion" when it is missing."""
    config = load_intake_config()
    providers = providers_config()
    try:
        threads = int(os.environ.get("TRIAGE_MODEL_THREADS", "2"))
    except ValueError:
        threads = 0  # the loader reports invalid configuration without stopping nurse review
    urgency = TriageUrgency.load(
        triage_model_path(),
        kind=os.environ.get("TRIAGE_MODEL_KIND", "auto"),
        device=os.environ.get("TRIAGE_MODEL_DEVICE", "cpu"),
        threads=threads,
    )
    events = OutboxEvents()
    ports = build_real_ports(
        identifier=CheckinIdentifier(),
        ehr=CachedEhrReader(),
        urgency=urgency,
        queue=QueuePort(queue),
        writeback=WritebackPort(events=events),
        briefing=briefing,
        events=events,
        intents=TurnClassifier(lambda: build_provider("conversation", providers)),
        clinic_qa=AgentClinicQA(ask, queue, events.publisher),
        nurse_flags=SqlNurseFlags(events.publisher),
    )

    def provider(task: str) -> str:
        spec = providers.tasks.get(task)
        if spec is None:
            return "not configured"
        body = spec.model_dump()
        described = f"{body.get('provider')} {body.get('model') or body.get('tool') or ''}".strip()
        if provider_mode() == "replay":  # CAREFLOW_PROVIDER_MODE: recorded replies, no model
            return f"replay (recorded from {described})"
        return described

    no_model = isinstance(urgency, NoTriageModel)
    choices = (
        PortChoice("identifier", "check-in (keypad)"),
        PortChoice("ehr", "FHIR R4 reader (M1-08)"),
        PortChoice(
            "conversation",
            "checklist",
            f"phrasing: {provider('conversation')}" if config.llm_phrasing else "templates only",
        ),
        PortChoice("extractor", "LLM extractor (M1-06)", provider("extraction")),
        PortChoice("rules", "careflow_rules", RULESET_VERSION),
        PortChoice(
            "urgency",
            "none (no suggestion)" if no_model else urgency.model_id,
            urgency.reason if no_model else urgency.model_version,
            degraded=no_model,
        ),
        PortChoice("queue", "queue (M1-10)"),
        PortChoice("writeback", "FHIR write-back (M1-09)"),
        PortChoice(
            "briefing",
            "briefing (M1-16)",
            f"summary: {provider('summary')}" if config.briefing_summary else "summary off",
        ),
        PortChoice("events", "transactional outbox (M1-11)"),
        PortChoice(
            "intents",
            "intent router (M2-10)",
            f"lexicon, then {provider('conversation')} for questions",
        ),
        PortChoice("clinic_qa", "ask the clinic (M2-09)", "phone only"),
        PortChoice("nurse_flags", "nurse_flags + nurse.flag (M2-10)"),
    )
    for choice in choices:
        logger.info(
            "intake.port",
            extra={"port": choice.port, "impl": choice.implementation, "detail": choice.detail},
        )
    version = "none" if no_model else urgency.model_version
    return Wiring(ports=ports, choices=choices, urgency_model_version=version)


__all__ = ["PortChoice", "Wiring", "build_ports", "triage_model_path"]
