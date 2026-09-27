"""`build_briefing(state)`: the doctor briefing as seven fixed sections of sourced items (M1-16).

Deterministic: every item's text comes from a fixed template over structured values, never from
a model (an optional summary is added later and checked, see `summary`). Every item carries its
provenance and source (safety rule 4), and every model-inferred item shows the nurse's level
beside it (safety rule 3). A section where nothing of its kind was recorded says so in a note:
an empty section is never shown as if it were complete.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from careflow_contracts.api_models import (
    Briefing,
    BriefingConfirmation,
    BriefingItem,
    BriefingNote,
    BriefingNoteCode,
    BriefingOverrideReason,
    BriefingSection,
    BriefingSectionId,
    BriefingSuggestion,
    BriefingTriage,
    BriefingVersions,
    SummaryStatus,
)
from careflow_contracts.generated.alert_schema import Alert, AlertSeverity
from careflow_contracts.generated.ehr_fetch_schema import EhrStatus
from careflow_contracts.generated.fact_schema import (
    CodeFact,
    FactBase,
    IntegerFact,
    ModelSource,
    NumberFact,
    NurseSource,
    Provenance,
    TextFact,
    TranscriptSpanSource,
    TriStateFact,
)
from careflow_contracts.generated.nurse_decision_schema import DecisionAction, NurseDecision
from careflow_contracts.generated.structured_facts_schema import (
    FactKey,
    StructuredFacts,
    Symptom,
)
from careflow_contracts.validators import iter_facts
from careflow_intake.state import IntakeState, structured_facts
from careflow_rules.news2 import PARAMETERS, News2Result, news2_inputs, score_news2

SECTION_TITLES: Final[dict[BriefingSectionId, str]] = {
    BriefingSectionId.alerts_triage: "Alerts and triage",
    BriefingSectionId.presenting_complaint: "Presenting complaint",
    BriefingSectionId.danger_screens: "Danger-screen answers",
    BriefingSectionId.vitals_news2: "Vitals and NEWS2",
    BriefingSectionId.record: "Record (EHR)",
    BriefingSectionId.model_output: "Model output",
    BriefingSectionId.data_quality: "Data quality",
}
TAGS: Final[dict[Provenance, str]] = {
    Provenance.EHR: "[EHR]",
    Provenance.PATIENT_STATED: "[Patient Stated]",
    Provenance.DEVICE_MEASURED: "[Device]",
    Provenance.NURSE_CONFIRMED: "[Nurse]",
    Provenance.MODEL_INFERRED: "[Model inferred]",
}
MAX_TEXT: Final = 500
MAX_QUOTE: Final = 160
SEVERITY_ORDER: Final = {AlertSeverity.critical: 0, AlertSeverity.high: 1}
SEVERITY_TEXT: Final = {AlertSeverity.critical: "Critical alert", AlertSeverity.high: "High alert"}
UNITS: Final = {"/min": "/min", "%": "%", "Cel": "°C", "mm[Hg]": "mmHg"}
SCREENS: Final = ("fast", "chest_pain", "infection", "child_danger", "child_amber")
SCREEN_LABELS: Final = {
    "fast": "FAST",
    "chest_pain": "Chest pain",
    "infection": "Infection",
    "child_danger": "Child red flag",
    "child_amber": "Child amber flag",
}
# Paths whose TextFact value is an ISO 8601 date-time or duration, shown relative to arrival.
TIME_PATHS: Final = frozenset(
    {"chest_pain.onset", "fast.last_known_well", "tasks.ecg_done_at", "symptoms.onset"}
)
LABELS: Final = {
    "patient.age_years": "Age",
    "patient.age_months": "Age",
    "patient.sex": "Sex",
    "chief_complaint": "Chief complaint",
    "complaint_category": "Complaint category",
    "symptoms.name": "Symptom",
    "symptoms.onset": "Symptom onset",
    "symptom_onset_at": "Onset",
    "severity": "Severity (0-10)",
    "pain_score": "Pain score",
    "pain_present": "Pain now",
    "pain_location": "Pain location",
    "injury": "Injury",
    "arrival_mode": "Arrival",
    "allergies_confirm": "Allergies (patient's answer)",
    "chest_pain.present": "Chest pain",
    "chest_pain.onset": "Chest pain onset",
    "fast.last_known_well": "Last known well",
    "vitals.heart_rate": "Heart rate",
    "vitals.spo2": "SpO2",
    "vitals.temperature_c": "Temperature",
    "vitals.respiratory_rate": "Respiratory rate",
    "vitals.systolic_bp": "Systolic blood pressure",
    "vitals.diastolic_bp": "Diastolic blood pressure",
    "vitals.acvpu": "ACVPU",
    "vitals.on_supplemental_o2": "Supplemental oxygen",
    "tasks.ecg_done_at": "ECG done",
    "ehr_context.allergies": "Allergy",
    "ehr_context.conditions": "Condition",
    "ehr_context.medications": "Medication",
}
NEWS2_LABELS: Final = {
    "respiratory_rate": "respiratory rate",
    "spo2": "SpO2",
    "systolic_bp": "systolic blood pressure",
    "heart_rate": "heart rate",
    "acvpu": "ACVPU",
    "temperature_c": "temperature",
    "on_supplemental_o2": "air or oxygen",
}
ACVPU_TEXT: Final = {
    "A": "A (alert)",
    "C": "C (new confusion)",
    "V": "V (responds to voice)",
    "P": "P (responds to pain)",
    "U": "U (unresponsive)",
}
RISK_TEXT: Final = {
    "low": "low risk",
    "low-medium": "low-medium risk",
    "medium": "medium risk",
    "high": "high risk",
}
REASON_TEXT: Final = {
    "clinical_judgement": "clinical judgement",
    "new_information": "new information",
    "patient_deteriorated": "patient deteriorated",
    "model_error": "model error",
    "other": "other",
}
NONE_RECORDED: Final[dict[BriefingSectionId, str]] = {
    BriefingSectionId.alerts_triage: "No safety alert was raised.",
    BriefingSectionId.presenting_complaint: "No complaint was recorded.",
    BriefingSectionId.danger_screens: "No danger-screen question was answered.",
    BriefingSectionId.vitals_news2: "No vital signs were recorded.",
    BriefingSectionId.record: "The EHR lists no allergies, conditions or medications.",
    BriefingSectionId.data_quality: "No data-quality problem was recorded.",
}
EHR_NOTES: Final[dict[EhrStatus, str]] = {
    EhrStatus.unavailable: "The EHR could not be read at check-in: this is not a complete record.",
    EhrStatus.not_found: "No EHR record was matched to the patient: this is not a complete record.",
    EhrStatus.ambiguous: "More than one EHR record matched the patient, so none was used.",
}
EHR_PARTIAL: Final = "The EHR was only partly read at check-in: the record may be incomplete."
_DURATION: Final = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$")


class BriefingNotReady(ValueError):
    """The briefing is built only after the nurse's decision (safety rule 3)."""


@dataclass
class Sections:
    items: dict[BriefingSectionId, list[BriefingItem]] = field(
        default_factory=lambda: {section: [] for section in SECTION_TITLES}
    )
    notes: dict[BriefingSectionId, list[BriefingNote]] = field(
        default_factory=lambda: {section: [] for section in SECTION_TITLES}
    )

    def add(self, section: BriefingSectionId, item: BriefingItem) -> None:
        self.items[section].append(item)

    def note(
        self, section: BriefingSectionId, code: BriefingNoteCode, text: str, **extra: Any
    ) -> None:
        self.notes[section].append(BriefingNote(code=code, text=text, **extra))


def level_text(decision: NurseDecision) -> str:
    return f"{decision.scale.value} {int(decision.final_level)}"


def clip(text: str, limit: int = MAX_TEXT) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def patient_words(fact: FactBase) -> str:
    """The patient's own words, for a patient-stated fact."""
    source = fact.source
    if not isinstance(source, TranscriptSpanSource):
        return ""
    return f' Patient: "{clip(source.quote, MAX_QUOTE)}"'


def missing_text(keys: Iterable[FactKey]) -> str:
    """NEWS2 inputs in words: `vitals.respiratory_rate` -> `respiratory rate`."""
    names = [key.value.removeprefix("vitals.") for key in keys]
    return ", ".join(NEWS2_LABELS.get(name, name.replace("_", " ")) for name in names)


def number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def minutes_between(earlier: datetime, later: datetime) -> int:
    return round((later - earlier).total_seconds() / 60)


def relative_time(value: str, arrived_at: datetime) -> str:
    """An ISO 8601 date-time as minutes before/after arrival; a duration in words; else as is."""
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        moment = None
    if moment is not None and moment.tzinfo is not None:
        minutes = minutes_between(arrived_at, moment)
        if minutes == 0:
            return "at arrival"
        span = f"{abs(minutes)} min" if abs(minutes) < 120 else f"{abs(minutes) / 60:.1f} h"
        return f"{span} {'after' if minutes > 0 else 'before'} arrival"
    if (found := _DURATION.match(value)) is not None and any(found.groups()):
        days, hours, mins = found.groups()
        parts = [
            f"{days} d" if days else "",
            f"{hours} h" if hours else "",
            f"{mins} min" if mins else "",
        ]
        return "for " + " ".join(part for part in parts if part)
    return value


def value_text(path: str, fact: FactBase, arrived_at: datetime) -> str:
    """The fact's value in words, per its contract type."""
    if isinstance(fact, TriStateFact):
        return fact.value.value
    if isinstance(fact, NumberFact):
        return f"{number(fact.value)} {UNITS.get(fact.unit, fact.unit)}"
    if isinstance(fact, IntegerFact):
        if path == "patient.age_years":
            return f"{fact.value} years"
        if path == "patient.age_months":
            return f"{fact.value} months"
        if path in ("pain_score", "severity"):
            return f"{fact.value}/10"
        return str(fact.value)
    if isinstance(fact, CodeFact):
        coding = fact.value
        code = str(getattr(coding.code, "value", coding.code))
        if path == "vitals.acvpu":
            return ACVPU_TEXT.get(code, code)
        text = coding.display or code.replace("_", " ")
        criticality = getattr(fact, "criticality", None)
        if criticality is None:
            return text
        return f"{text} ({getattr(criticality, 'value', criticality)} criticality)"
    if isinstance(fact, TextFact):
        if path in TIME_PATHS:
            return relative_time(fact.value, arrived_at)
        return fact.value.replace("_", " ") if path == "symptoms.name" else fact.value
    return str(getattr(fact, "value", ""))


def plain_path(path: str) -> str:
    """A path without list positions: `symptoms[0].name` -> `symptoms.name`."""
    return re.sub(r"\[\d+\]", "", path)


def section_for(path: str) -> BriefingSectionId:
    group = path.split(".", 1)[0]
    if group in SCREENS:
        return BriefingSectionId.danger_screens
    if group == "vitals":
        return BriefingSectionId.vitals_news2
    if group == "tasks":
        return BriefingSectionId.alerts_triage
    if group == "ehr_context" or path == "allergies_confirm":
        return BriefingSectionId.record
    if path == "complaint_category":
        return BriefingSectionId.model_output
    return BriefingSectionId.presenting_complaint


def label_for(path: str, facts: StructuredFacts, raw_path: str) -> str:
    if path in LABELS:
        label = LABELS[path]
    else:
        group, _, name = path.partition(".")
        label = f"{SCREEN_LABELS.get(group, group)}, {name.replace('_', ' ')}"
    if path == "symptoms.onset":
        index = int(re.search(r"\[(\d+)\]", raw_path).group(1))  # type: ignore[union-attr]
        symptom: Symptom = (facts.symptoms or [])[index]
        label = f"{label} ({symptom.name.value.replace('_', ' ')})"
    return label


def fact_item(
    raw_path: str, fact: FactBase, facts: StructuredFacts, state: IntakeState, nurse_level: str
) -> BriefingItem:
    path = plain_path(raw_path)
    text = f"{label_for(path, facts, raw_path)}: {value_text(path, fact, state['arrived_at'])}."
    if path == "symptoms.name":
        index = int(re.search(r"\[(\d+)\]", raw_path).group(1))  # type: ignore[union-attr]
        severity = (facts.symptoms or [])[index].severity
        if severity is not None:
            text = f"{text[:-1]} ({severity.value})."
    if fact.provenance is Provenance.MODEL_INFERRED:
        text += f" Nurse's level: {nurse_level}."
    text += patient_words(fact)
    return BriefingItem(
        text=clip(text),
        provenance=fact.provenance,
        source=fact.source,
        fact_id=fact.fact_id,
        recorded_at=fact.recorded_at,
    )


def nurse_item(decision: NurseDecision, text: str) -> BriefingItem:
    return BriefingItem(
        text=clip(text),
        provenance=Provenance.NURSE_CONFIRMED,
        source=NurseSource(kind="nurse", staff_user_id=decision.nurse_user_id),
        recorded_at=decision.decided_at,
    )


def alert_items(
    state: IntakeState, decision: NurseDecision, by_id: dict[str, FactBase]
) -> Iterator[BriefingItem]:
    """Critical first, then by time raised. An alert the nurse acknowledged is tagged [Nurse];
    otherwise it carries the source of its first evidence fact."""
    alerts: list[Alert] = sorted(
        state.get("alerts", []), key=lambda a: (SEVERITY_ORDER[a.severity], a.raised_at)
    )
    acknowledged = set(decision.acknowledged_alert_ids or [])
    for alert in alerts:
        raised = minutes_between(state["arrived_at"], alert.raised_at)
        text = f"{SEVERITY_TEXT[alert.severity]}: {alert.display_text or alert.rule_id.value}"
        text += f" Raised {raised} min after arrival."
        if alert.partial and alert.missing_inputs:
            text += f" Raised on partial inputs (missing: {missing_text(alert.missing_inputs)})."
        evidence = [by_id[i] for i in alert.evidence_fact_ids if i in by_id]
        if alert.alert_id in acknowledged:
            yield nurse_item(decision, text + " Acknowledged by the nurse.")
        elif evidence:
            first = evidence[0]
            yield BriefingItem(
                text=clip(text),
                provenance=first.provenance,
                source=first.source,
                recorded_at=first.recorded_at,
            )
        else:  # fail closed: shown, and marked as having no valid source
            item = nurse_item(decision, text)
            yield item.model_copy(update={"source_missing": True})


def triage_item(state: IntakeState, decision: NurseDecision) -> BriefingItem:
    suggestion = state.get("suggestion")
    if decision.action is DecisionAction.CONFIRM:
        text = f"Triage level: {level_text(decision)}, confirmed by the nurse."
    elif suggestion is None:
        text = f"Triage level: {level_text(decision)}, set by the nurse (no model suggestion)."
    else:
        text = (
            f"Triage level: {level_text(decision)}, set by the nurse "
            f"(model suggested {suggestion.scale.value} {int(suggestion.level)})."
        )
    if decision.override_reason_code is not None:
        text += f" Reason: {REASON_TEXT[decision.override_reason_code.value]}."
    if decision.override_note:
        text += f' Note: "{clip(decision.override_note, MAX_QUOTE)}"'
    return nurse_item(decision, text)


def news2_item(facts: StructuredFacts, score: News2Result) -> BriefingItem | None:
    """The rules engine's NEWS2 over the current vitals, sourced to its latest input: a nurse
    entry when the nurse completed it, else the latest device reading."""
    vitals = facts.vitals
    inputs: list[FactBase] = [
        fact for p in PARAMETERS if vitals and (fact := getattr(vitals, p)) is not None
    ]
    if not inputs:
        return None
    nurse = [f for f in inputs if f.provenance is Provenance.NURSE_CONFIRMED]
    latest = max(nurse or inputs, key=lambda f: f.recorded_at)
    if score.partial:
        text = (
            f"Partial NEWS2 {score.total} from {7 - len(score.missing)} of 7 parameters "
            f"(missing: {missing_text(score.missing)}). Not a full score."
        )
    else:
        text = f"NEWS2 {score.total} ({RISK_TEXT[score.risk_band]})."
        if score.any_single_3:
            text += " One parameter scores 3."
    return BriefingItem(
        text=text,
        provenance=latest.provenance,
        source=latest.source,
        recorded_at=latest.recorded_at,
    )


def model_items(state: IntakeState, nurse_level: str) -> Iterator[BriefingItem]:
    suggestion = state.get("suggestion")
    if suggestion is None:
        return
    source = ModelSource(
        kind="model", model_id=suggestion.model_id, model_version=suggestion.model_version
    )
    label = f"{suggestion.scale.value} {int(suggestion.level)}"
    text = (
        f"Suggested {label} (confidence {suggestion.confidence:.2f}). "
        f"Only a suggestion. Nurse's level: {nurse_level}."
    )
    yield BriefingItem(
        text=text,
        provenance=Provenance.MODEL_INFERRED,
        source=source,
        recorded_at=suggestion.created_at,
    )
    for reason in suggestion.reasons:
        yield BriefingItem(
            text=clip(f"Reason: {reason.text}. Nurse's level: {nurse_level}."),
            provenance=Provenance.MODEL_INFERRED,
            source=source,
            recorded_at=suggestion.created_at,
        )


def data_quality(state: IntakeState, sections: Sections) -> None:
    flags = state.get("flags", {})
    section = BriefingSectionId.data_quality
    if flags.get("extraction_incomplete"):
        sections.note(
            section,
            BriefingNoteCode.extraction_incomplete,
            "Extraction incomplete: some answers could not be turned into facts. "
            "Check them with the patient.",
        )
    dropped = int(state.get("extraction", {}).get("dropped_count", 0) or 0)
    if dropped:
        sections.note(
            section,
            BriefingNoteCode.facts_dropped,
            f"{dropped} statement{'s' if dropped != 1 else ''} failed the extraction check "
            "and were left out.",
            count=dropped,
        )
    if flags.get("intake_truncated"):
        sections.note(
            section,
            BriefingNoteCode.intake_stopped_early,
            "The intake stopped before every question was asked (turn or time limit).",
        )
    ehr = state.get("ehr_status")
    status = EhrStatus(ehr) if ehr is not None else None
    if status is EhrStatus.partial:
        sections.note(section, BriefingNoteCode.ehr_partial, EHR_PARTIAL)
    elif flags.get("ehr_unavailable") or status in EHR_NOTES:
        text = EHR_NOTES.get(status or EhrStatus.unavailable, EHR_NOTES[EhrStatus.unavailable])
        sections.note(section, BriefingNoteCode.ehr_unavailable, text)
    if not sections.notes[section]:
        sections.note(
            section, BriefingNoteCode.none_recorded, NONE_RECORDED[BriefingSectionId.data_quality]
        )


def triage(state: IntakeState, decision: NurseDecision) -> BriefingTriage:
    suggestion = state.get("suggestion")
    suggested = (
        BriefingSuggestion(
            level=suggestion.level,
            scale=suggestion.scale,
            confidence=suggestion.confidence,
            model_id=suggestion.model_id,
            model_version=suggestion.model_version,
            **({"display_label": suggestion.display_label} if suggestion.display_label else {}),
        )
        if suggestion is not None
        else None
    )
    reason = (
        BriefingOverrideReason(
            code=decision.override_reason_code,
            **({"note": decision.override_note} if decision.override_note else {}),
        )
        if decision.override_reason_code is not None
        else None
    )
    return BriefingTriage(
        suggested=suggested,
        confirmed=BriefingConfirmation(
            level=decision.final_level,
            scale=decision.scale,
            action=decision.action,
            nurse_user_id=decision.nurse_user_id,
            decided_at=decision.decided_at,
        ),
        override_reason=reason,
    )


def ruleset_version(state: IntakeState) -> str | None:
    if version := state.get("ruleset_version"):
        return version
    return next((alert.ruleset_version for alert in state.get("alerts", [])), None)


def briefing_json(briefing: Briefing) -> dict[str, Any]:
    """The briefing as contract JSON: an optional field without a value is left out, and the
    fields the contract requires but allows to be null are kept as null."""
    body = briefing.model_dump(mode="json", exclude_none=True)
    body.setdefault("summary", None)
    for name in ("suggested", "override_reason"):
        body["triage"].setdefault(name, None)
    for name in ("ruleset", "model", "prompt"):
        body["versions"].setdefault(name, None)
    return body


def build_briefing(state: IntakeState, *, now: datetime) -> Briefing:
    """The plain briefing: every section, no summary yet (`summary_status` disabled)."""
    decision = state.get("nurse_decision")
    if decision is None:
        raise BriefingNotReady("the briefing is built after the nurse's decision")
    facts = structured_facts(state)
    nurse_level = level_text(decision)
    by_id = {fact.fact_id: fact for _, fact in iter_facts(facts)}
    sections = Sections()

    for item in alert_items(state, decision, by_id):
        sections.add(BriefingSectionId.alerts_triage, item)
    if not state.get("alerts"):
        sections.note(
            BriefingSectionId.alerts_triage,
            BriefingNoteCode.none_recorded,
            NONE_RECORDED[BriefingSectionId.alerts_triage],
        )
    sections.add(BriefingSectionId.alerts_triage, triage_item(state, decision))

    for raw_path, fact in iter_facts(facts):
        path = plain_path(raw_path)
        sections.add(section_for(path), fact_item(raw_path, fact, facts, state, nurse_level))

    score = score_news2(news2_inputs(facts))
    if score is None:
        sections.note(
            BriefingSectionId.vitals_news2,
            BriefingNoteCode.news2_not_applicable,
            "NEWS2 does not apply under 16 (NICE NG143 signs are in the danger screens).",
        )
    elif (item := news2_item(facts, score)) is not None:
        sections.add(BriefingSectionId.vitals_news2, item)

    for item in model_items(state, nurse_level):
        sections.add(BriefingSectionId.model_output, item)
    if state.get("suggestion") is None:
        sections.note(
            BriefingSectionId.model_output,
            BriefingNoteCode.suggestion_unavailable,
            "No model suggestion: the nurse set the level without one.",
        )

    ehr = state.get("ehr_status")
    status = EhrStatus(ehr) if ehr is not None else None
    if status is not None and status in EHR_NOTES:
        sections.note(BriefingSectionId.record, BriefingNoteCode.ehr_unavailable, EHR_NOTES[status])
    elif status is EhrStatus.partial:
        sections.note(BriefingSectionId.record, BriefingNoteCode.ehr_partial, EHR_PARTIAL)
    data_quality(state, sections)

    for section in (
        BriefingSectionId.presenting_complaint,
        BriefingSectionId.danger_screens,
        BriefingSectionId.vitals_news2,
    ):
        if not sections.items[section] and not sections.notes[section]:
            sections.note(section, BriefingNoteCode.none_recorded, NONE_RECORDED[section])
    record = BriefingSectionId.record
    if not any(item.provenance is Provenance.EHR for item in sections.items[record]) and (
        not sections.notes[record]
    ):
        sections.note(record, BriefingNoteCode.none_recorded, NONE_RECORDED[record])

    return Briefing(
        session_id=state["session_id"],
        generated_at=now,
        sections=[
            BriefingSection(
                id=section,
                title=title,
                items=sections.items[section],
                **({"notes": sections.notes[section]} if sections.notes[section] else {}),
            )
            for section, title in SECTION_TITLES.items()
        ],
        summary=None,
        summary_status=SummaryStatus.disabled,
        triage=triage(state, decision),
        versions=BriefingVersions(
            ruleset=ruleset_version(state),
            model=suggestion.model_version if (suggestion := state.get("suggestion")) else None,
            prompt=None,
        ),
    )


__all__ = [
    "SECTION_TITLES",
    "TAGS",
    "BriefingNotReady",
    "briefing_json",
    "build_briefing",
    "level_text",
    "relative_time",
]
