"""`build_ports`: the intake graph's ports in the API (M1-12).

It extends `careflow_intake.wiring.build_real_ports` (M1-07: the LLM extractor, the rules engine
and the checklist conversation) with the API's own adapters: check-in identity, the FHIR EHR
reader (M1-08), the urgency model (M1-04, from TRIAGE_MODEL_PATH, or no suggestion), the queue
(M1-10), the write-back (M1-09), the briefing (M1-16) and the outbox events (M1-11). The choice for
every port is logged at startup and shown on the admin status page.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

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
from careflow_api.queue.service import QueueService
from careflow_intake.ports import IntakePorts
from careflow_intake.wiring import build_real_ports, load_intake_config, providers_config
from careflow_rules import RULESET_VERSION

logger = logging.getLogger("careflow_api.intake")

REPO: Final = Path(__file__).resolve().parents[5]
DEFAULT_TRIAGE_MODEL: Final = REPO / "services" / "triage" / "artifacts" / "ktas-hgb-ebf31b5e-s42"


def triage_model_path() -> Path:
    return Path(os.environ.get("TRIAGE_MODEL_PATH") or DEFAULT_TRIAGE_MODEL)


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


def build_ports(*, queue: QueueService, briefing: BriefingService) -> Wiring:
    """The real ports; the urgency model falls back to "no suggestion" when it is missing."""
    config = load_intake_config()
    providers = providers_config()
    urgency = TriageUrgency.load(triage_model_path())
    events = OutboxEvents()
    ports = build_real_ports(
        identifier=CheckinIdentifier(),
        ehr=CachedEhrReader(),
        urgency=urgency,
        queue=QueuePort(queue),
        writeback=WritebackPort(events=events),
        briefing=briefing,
        events=events,
    )

    def provider(task: str) -> str:
        spec = providers.tasks.get(task)
        if spec is None:
            return "not configured"
        body = spec.model_dump()
        return f"{body.get('provider')} {body.get('model') or body.get('tool') or ''}".strip()

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
            "none (no suggestion)" if no_model else "trained model (M1-04)",
            str(triage_model_path().name) if not no_model else "TRIAGE_MODEL_PATH has no model",
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
    )
    for choice in choices:
        logger.info(
            "intake.port",
            extra={"port": choice.port, "impl": choice.implementation, "detail": choice.detail},
        )
    version = "none" if no_model else urgency.model_version
    return Wiring(ports=ports, choices=choices, urgency_model_version=version)


__all__ = ["PortChoice", "Wiring", "build_ports", "triage_model_path"]
