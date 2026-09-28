"""The doctor briefing (M1-16): build, check, then optionally summarise.

`make_briefing(state)` builds the plain briefing from fixed templates (`builder`), marks any item
whose source cannot be verified (`check`), and adds a checked summary when a `Summarizer` is given
(`summary`). The plain list is always complete; the summary never adds information.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from careflow_contracts.api_models import Briefing, SummaryStatus
from careflow_intake.briefing.builder import BriefingNotReady, build_briefing
from careflow_intake.briefing.check import check_briefing
from careflow_intake.briefing.summary import Summarizer, SummaryOutcome
from careflow_intake.state import IntakeState


@dataclass(frozen=True)
class BriefingResult:
    briefing: Briefing
    #: Items marked "source missing" (logged as errors; the tests require none).
    problems: list[str] = field(default_factory=list)
    #: Why the summary was dropped or disabled.
    summary_reasons: list[str] = field(default_factory=list)


async def make_briefing(
    state: IntakeState, *, now: datetime, summarizer: Summarizer | None = None
) -> BriefingResult:
    """The checked briefing. Without a summarizer (`briefing_summary: false`) the summary is
    `disabled`."""
    checked, problems = check_briefing(build_briefing(state, now=now), state)
    if state.get("reception_context") is not None or state.get("urgent_help") is not None:
        return BriefingResult(checked, problems, ["structured reception uses the sourced briefing"])
    if summarizer is None:
        return BriefingResult(checked, problems, ["briefing_summary is off"])
    outcome: SummaryOutcome = await summarizer(checked)
    versions = checked.versions.model_copy(update={"prompt": outcome.prompt_version})
    briefing = checked.model_copy(
        update={
            "summary": outcome.summary if outcome.status is SummaryStatus.ok else None,
            "summary_status": outcome.status,
            "versions": versions,
        }
    )
    return BriefingResult(briefing, problems, outcome.reasons)


__all__ = ["BriefingNotReady", "BriefingResult", "Summarizer", "make_briefing"]
