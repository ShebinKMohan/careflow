"""`pick_next_question`: code, not the LLM, decides what to ask (M1-07).

Order: the complaint first; then the danger screens the rules ask the patient for, the screen of
the most severe alert first; then the rest of the checklist. Once a critical alert is raised only
the remaining screens are asked. A key is never asked twice, except one re-ask when the first
answer gave no fact.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

from careflow_contracts.generated.alert_schema import Alert, AlertSeverity, RuleId
from careflow_contracts.generated.fact_schema import FactBase, TriState, TriStateFact
from careflow_contracts.generated.structured_facts_schema import (
    EhrContext,
    FactKey,
    NeededFact,
    NeededFactAudience,
    StructuredFacts,
)
from careflow_contracts.validators import iter_facts
from careflow_intake.conversation.checklist import CHECKLIST

type QuestionKey = FactKey  # every question asks for one StructuredFacts fact path

MAX_ASKS: Final = 2  # the question, and one re-ask when the answer gave no fact
# Each screen reason, the rules it serves (in rule order) and its fallback rank.
SCREENS: Final[dict[str, tuple[RuleId, ...]]] = {
    "fast_screen": (RuleId.FAST_STROKE,),
    "chest_pain_screen": (RuleId.CHEST_PAIN_ECG_10MIN,),
    "ng143_parent_screen": (RuleId.NG143_RED_FLAG, RuleId.NG143_AMBER_FLAG),
}
SEVERITY_RANK: Final = {AlertSeverity.critical: 0, AlertSeverity.high: 1}


def answered_keys(facts: StructuredFacts) -> set[FactKey]:
    """Keys with a fact; a list key counts once it has an item (`symptoms[0].name` -> symptoms)."""
    values = {key.value for key in FactKey}
    heads = (path.split("[", 1)[0] for path, _ in iter_facts(facts))
    return {FactKey(head) for head in heads if head in values}


def _value(facts: StructuredFacts, key: FactKey) -> object:
    for path, fact in iter_facts(facts):
        if path == key.value:
            return _plain(fact)
    return None


def _plain(fact: FactBase) -> object:
    if isinstance(fact, TriStateFact):
        return fact.value
    return getattr(fact, "value", None)


def has_critical_alert(alerts: Sequence[Alert]) -> bool:
    return any(alert.severity is AlertSeverity.critical for alert in alerts)


def _screen_rank(need: NeededFact, alerts: Sequence[Alert], position: int) -> tuple[int, int, int]:
    """Most severe raised alert first, then rule order, then the order the rules asked in."""
    rules = SCREENS.get(need.reason, ())
    severities = [SEVERITY_RANK[a.severity] for a in alerts if a.rule_id in rules]
    order = list(SCREENS).index(need.reason) if need.reason in SCREENS else len(SCREENS)
    return (min(severities, default=len(SEVERITY_RANK)), order, position)


def _skip_checklist_key(key: FactKey, facts: StructuredFacts, answered: set[FactKey]) -> bool:
    """Checklist questions that the answers so far make pointless."""
    pain_present = _value(facts, FactKey.pain_present)
    pain_score = _value(facts, FactKey.pain_score)
    no_pain = pain_present is TriState.no or pain_score == 0
    if key is FactKey.symptom_onset_at:
        return bool({FactKey.chest_pain_onset, FactKey.fast_last_known_well} & answered)
    if key is FactKey.pain_score:
        return pain_present is TriState.no
    if key is FactKey.severity:
        return FactKey.pain_score in answered  # the pain score is the severity of a painful problem
    if key is FactKey.pain_location:
        chest_pain = _value(facts, FactKey.chest_pain_present) is TriState.yes
        has_pain = pain_present is TriState.yes or (isinstance(pain_score, int) and pain_score > 0)
        return no_pain or chest_pain or not has_pain
    return False


def pick_next_question(
    facts: StructuredFacts,
    needed_facts: Sequence[NeededFact],
    asked: Mapping[FactKey, int],
    ehr_context: EhrContext | None = None,
    *,
    alerts: Sequence[Alert] = (),
    askable: set[FactKey] | None = None,
) -> QuestionKey | None:
    """The next key to ask about, or None when nothing is left. `asked` counts the times each key
    was asked; `askable` limits the keys to those with an approved template."""
    answered = answered_keys(facts)

    def can_ask(key: FactKey) -> bool:
        if key in answered or (askable is not None and key not in askable):
            return False
        return asked.get(key, 0) < MAX_ASKS

    if can_ask(FactKey.chief_complaint):
        return FactKey.chief_complaint
    screens = [
        (need, position)
        for position, need in enumerate(needed_facts)
        if need.audience is NeededFactAudience.patient and can_ask(need.key)
    ]
    screens.sort(key=lambda item: _screen_rank(item[0], alerts, item[1]))
    if screens:
        return screens[0][0].key
    if has_critical_alert(alerts):
        return None  # a nurse is coming: only the danger screens are asked
    for key in CHECKLIST:
        if can_ask(key) and not _skip_checklist_key(key, facts, answered):
            return key
    return None


__all__ = ["MAX_ASKS", "QuestionKey", "answered_keys", "has_critical_alert", "pick_next_question"]
