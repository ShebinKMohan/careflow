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
from careflow_intake.briefing.summary import DEFAULT_TIMEOUT_SECONDS as DEFAULT_SUMMARY_TIMEOUT
from careflow_intake.briefing.summary import TASK as SUMMARY_TASK
from careflow_intake.briefing.summary import Summarizer
from careflow_intake.conversation import ChecklistConversation
from careflow_intake.conversation.phrasing import DEFAULT_TIMEOUT_SECONDS, Phraser
from careflow_intake.extraction.extractor import LlmExtractor
from careflow_intake.ports import IntakePorts, RulesOutcome, utc_now
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
        return RulesOutcome(
            alerts=list(result.alerts),
            needed_facts=list(result.needed_facts),
            ruleset_version=result.ruleset_version,
        )


@dataclass(frozen=True)
class IntakeConfig:
    llm_phrasing: bool = True
    phrasing_timeout_s: float = DEFAULT_TIMEOUT_SECONDS
    max_turns: int = 8
    max_seconds: float = 120.0
    briefing_summary: bool = True
    summary_timeout_s: float = DEFAULT_SUMMARY_TIMEOUT

    @property
    def settings(self) -> IntakeSettings:
        return IntakeSettings(max_turns=self.max_turns, max_seconds=self.max_seconds)


def intake_config_path() -> Path:
    """`CAREFLOW_INTAKE_CONFIG` when set (deterministic API tests and E2E runs use a copy with
    `llm_phrasing: false`), else config/intake.yaml."""
    override = os.environ.get("CAREFLOW_INTAKE_CONFIG")
    return Path(override) if override else INTAKE_CONFIG


def load_intake_config(path: Path | None = None) -> IntakeConfig:
    path = path or intake_config_path()
    body: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return IntakeConfig(
        llm_phrasing=bool(body.get("llm_phrasing", True)),
        phrasing_timeout_s=float(body.get("phrasing_timeout_s", DEFAULT_TIMEOUT_SECONDS)),
        max_turns=int(body.get("max_turns", 8)),
        max_seconds=float(body.get("max_seconds", 120)),
        briefing_summary=bool(body.get("briefing_summary", True)),
        summary_timeout_s=float(body.get("summary_timeout_s", DEFAULT_SUMMARY_TIMEOUT)),
    )


def briefing_summarizer(
    config: IntakeConfig | None = None, providers: ProvidersConfig | None = None
) -> Summarizer | None:
    """The briefing's `Summarizer` for the `summary` task, or None when `briefing_summary` is
    off. The provider is built on first use, so a missing API key disables the summary instead
    of failing the briefing."""
    config = config or load_intake_config()
    if not config.briefing_summary:
        return None
    resolved = providers or providers_config()
    return Summarizer(
        provider=lambda: build_provider(SUMMARY_TASK, resolved),
        timeout_seconds=config.summary_timeout_s,
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
    intake: Path | None = None  # None: intake_config_path()
    llm_phrasing: bool | None = None  # overrides config/intake.yaml
    extraction_provider: ChatProvider | None = None  # e.g. a ReplayProvider for scripted runs
    conversation_provider: ChatProvider | None = None


def build_real_ports(settings: RealPortsSettings | None = None, **stubs: Any) -> IntakePorts:
    """IntakePorts with the real extractor, rules, conversation and clock; keyword arguments
    replace any of the remaining stubs (a test may pass `clock=SteppingClock()`).

    The clock is the real UTC clock: it dates every turn, fact, alert (`raised_at`), suggestion
    and completion. The stubs' stepping clock (starting 27 Sep 2026 09:00Z) must never reach a
    running API, where it dated alerts hours before the patient arrived."""
    stubs.setdefault("clock", utc_now)
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
    "briefing_summarizer",
    "build_real_ports",
    "intake_config_path",
    "load_intake_config",
    "providers_config",
]
