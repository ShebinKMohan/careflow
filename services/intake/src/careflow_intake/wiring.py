"""`build_real_ports`: the real ports of M1-06/M1-07 in the M1-05 graph.

Real: the LLM extractor, the safety rules (`careflow_rules.evaluate`) and the checklist
conversation. Still stubs until their issues land: patient identity and EHR (M1-08), urgency
model (M1-12), queue (M1-10), write-back (M1-09), briefing (M1-16) and events (M1-11).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import yaml

from careflow_contracts.generated.structured_facts_schema import StructuredFacts
from careflow_intake.conversation import ChecklistConversation
from careflow_intake.conversation.phrasing import DEFAULT_TIMEOUT_SECONDS, Phraser
from careflow_intake.extraction.extractor import LlmExtractor
from careflow_intake.ports import IntakePorts, RulesOutcome
from careflow_intake.state import IntakeSettings
from careflow_intake.stubs import stub_ports
from careflow_providers.base import ChatProvider
from careflow_providers.config import LocalTaskConfig, ProvidersConfig, load_config
from careflow_providers.factory import build_provider
from careflow_rules import evaluate

REPO: Final = Path(__file__).resolve().parents[4]
PROVIDERS_CONFIG: Final = REPO / "config" / "providers.yaml"
INTAKE_CONFIG: Final = REPO / "config" / "intake.yaml"


class CareflowRules:
    """The `RulesRunner` port over the deterministic rules engine (M1-01/M1-02)."""

    def run(
        self, facts: StructuredFacts, *, now: datetime, arrived_at: datetime | None
    ) -> RulesOutcome:
        result = evaluate(facts, now, arrived_at=arrived_at)
        return RulesOutcome(alerts=list(result.alerts), needed_facts=list(result.needed_facts))


@dataclass(frozen=True)
class IntakeConfig:
    llm_phrasing: bool = True
    phrasing_timeout_s: float = DEFAULT_TIMEOUT_SECONDS
    max_turns: int = 8
    max_seconds: float = 120.0

    @property
    def settings(self) -> IntakeSettings:
        return IntakeSettings(max_turns=self.max_turns, max_seconds=self.max_seconds)


def load_intake_config(path: Path = INTAKE_CONFIG) -> IntakeConfig:
    body: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return IntakeConfig(
        llm_phrasing=bool(body.get("llm_phrasing", True)),
        phrasing_timeout_s=float(body.get("phrasing_timeout_s", DEFAULT_TIMEOUT_SECONDS)),
        max_turns=int(body.get("max_turns", 8)),
        max_seconds=float(body.get("max_seconds", 120)),
    )


def providers_config(path: Path = PROVIDERS_CONFIG) -> ProvidersConfig:
    """providers.yaml, with every local task pointed at CAREFLOW_LOCAL_LLM_BASE_URL when it is set
    (scripts/with_llm.sh sets it)."""
    providers = load_config(path)
    base_url = os.environ.get("CAREFLOW_LOCAL_LLM_BASE_URL")
    if not base_url:
        return providers
    tasks = {
        task: config.model_copy(update={"base_url": base_url})
        if isinstance(config, LocalTaskConfig)
        else config
        for task, config in providers.tasks.items()
    }
    return providers.model_copy(update={"tasks": tasks})


@dataclass(frozen=True)
class RealPortsSettings:
    providers: Path = PROVIDERS_CONFIG
    intake: Path = INTAKE_CONFIG
    llm_phrasing: bool | None = None  # overrides config/intake.yaml
    extraction_provider: ChatProvider | None = None  # e.g. a ReplayProvider for scripted runs
    conversation_provider: ChatProvider | None = None


def build_real_ports(settings: RealPortsSettings | None = None, **stubs: Any) -> IntakePorts:
    """IntakePorts with the real extractor, rules and conversation; keyword arguments replace any
    of the remaining stubs."""
    settings = settings or RealPortsSettings()
    config = load_intake_config(settings.intake)
    providers = providers_config(settings.providers)
    if settings.extraction_provider is not None:
        extractor = LlmExtractor(settings.extraction_provider)
    else:
        extractor = LlmExtractor.from_config(providers)
    phrasing = config.llm_phrasing if settings.llm_phrasing is None else settings.llm_phrasing
    provider = settings.conversation_provider
    if provider is None and phrasing:
        provider = build_provider("conversation", providers)
    phraser = Phraser(provider, enabled=phrasing, timeout_seconds=config.phrasing_timeout_s)
    return stub_ports(
        extractor=extractor,
        rules=CareflowRules(),
        conversation=ChecklistConversation(phraser=phraser),
        **stubs,
    )


__all__ = [
    "INTAKE_CONFIG",
    "PROVIDERS_CONFIG",
    "CareflowRules",
    "IntakeConfig",
    "RealPortsSettings",
    "build_real_ports",
    "load_intake_config",
    "providers_config",
]
