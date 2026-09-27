"""Generate the seven persona fixtures for the briefing tests (M1-16). Synthetic data only.

    uv run python services/intake/tests/fixtures/briefing/generate.py

Each persona is a scripted intake: the patient's answers (facts with transcript spans into the
turns), kiosk device vitals, nurse entries, the EHR record from `infra/seed/personas/<id>.json`
read through the real EHR mapper (M1-08), alerts from the real rules engine (M1-01/M1-02) run
the way the graph runs it, and the suggestion from the trained urgency model (M1-04, a local
git-ignored artifact) when it can suggest. The nurse's decision is scripted. The output is
committed; this script is only rerun when the personas change.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from careflow_contracts.generated.ehr_fetch_schema import EhrStatus
from careflow_contracts.generated.nurse_decision_schema import NurseDecision
from careflow_contracts.generated.structured_facts_schema import (
    EhrContext,
    FactKey,
    Language,
    Turn,
    TurnRole,
)
from careflow_contracts.generated.urgency_suggestion_schema import TriageScale, UrgencySuggestion
from careflow_contracts.ids import stable_fact_id
from careflow_contracts.validators import check_suggestion_evidence
from careflow_intake.briefing.record import FIXTURES, state_to_json
from careflow_intake.extraction.prompt import PROMPT_VERSION as EXTRACTION_PROMPT
from careflow_intake.facts import FactEntry
from careflow_intake.state import IntakeState, merge_alerts_by_rule_id, structured_facts
from careflow_integration.adapters import mapper
from careflow_integration.adapters.loader import load_adapter
from careflow_rules import RULESET_VERSION, evaluate
from careflow_triage.inference import OutOfScopeError, UrgencyModel
from careflow_triage.ktas.features import ACVPU_SYSTEM, COMPLAINT_SYSTEM, complaint_category
from careflow_triage.paths import ARTIFACTS_DIR, REPO_ROOT

MODEL_ARTIFACT: Final = ARTIFACTS_DIR / "ktas-hgb-ebf31b5e-s42"
SEED: Final = REPO_ROOT / "infra" / "seed" / "personas"
CLINIC: Final = "clinic_demo"
DEVICE: Final = "sim_vitals_01"
NURSE: Final = "usr_nurse_01"
EXTRACTION_MODEL: Final = "qwen3.5-9b-4bit"
KEYWORD_MODEL: Final = {"kind": "model", "model_id": "ktas_keyword_map", "model_version": "1"}
UNITS: Final = {
    "heart_rate": "/min",
    "respiratory_rate": "/min",
    "spo2": "%",
    "temperature_c": "Cel",
    "systolic_bp": "mm[Hg]",
    "diastolic_bp": "mm[Hg]",
}
ACVPU_DISPLAY: Final = {
    "A": "Alert",
    "C": "New confusion",
    "V": "Voice",
    "P": "Pain",
    "U": "Unresponsive",
}


@dataclass(frozen=True)
class Stated:
    """A fact the patient stated: its key, the words it rests on, and its value."""

    key: str
    quote: str
    value: Any


@dataclass(frozen=True)
class Answer:
    question_key: str
    question: str
    answer: str
    facts: tuple[Stated, ...]


@dataclass(frozen=True)
class Persona:
    id: str
    description: str
    arrived_at: datetime
    answers: tuple[Answer, ...]
    device: dict[str, float] = field(default_factory=dict)
    nurse_vitals: dict[str, Any] = field(default_factory=dict)
    nurse_facts: dict[str, Any] = field(default_factory=dict)
    ehr: EhrStatus = EhrStatus.ok
    flags: dict[str, bool] = field(default_factory=dict)
    dropped: int = 0
    #: (suggestion or None) -> (action, final level, reason code, note)
    decide: Callable[[UrgencySuggestion | None], tuple[str, int, str | None, str | None]] = (
        lambda s: (
            ("CONFIRM", int(s.level), None, None)
            if s is not None
            else ("OVERRIDE", 3, "other", "no model suggestion")
        )
    )

    @property
    def session_id(self) -> str:
        return "ses_" + self.id.replace("-", "_")


def at(hour: int, minute: int) -> datetime:
    return datetime(2026, 9, 27, hour, minute, tzinfo=UTC)


def at_most(level: int, note: str | None) -> Callable[[UrgencySuggestion | None], Any]:
    """The nurse confirms a suggestion at `level` or more urgent, else sets `level`."""

    def decide(suggestion: UrgencySuggestion | None) -> tuple[str, int, str | None, str | None]:
        if suggestion is not None and int(suggestion.level) <= level:
            return ("CONFIRM", int(suggestion.level), None, None)
        return ("OVERRIDE", level, "clinical_judgement", note)

    return decide


PERSONAS: Final = (
    Persona(
        id="chest-pain",
        description="Crushing chest pain radiating to the left arm with sweating; ECG done by the "
        "nurse at 7 minutes; full NEWS2 after the nurse's vitals.",
        arrived_at=at(9, 0),
        answers=(
            Answer(
                "chief_complaint",
                "What brings you in today?",
                "I have a crushing pain in my chest, it started about an hour ago.",
                (
                    Stated(
                        "chief_complaint", "crushing pain in my chest", "crushing pain in my chest"
                    ),
                    Stated("chest_pain.present", "crushing pain in my chest", "yes"),
                    Stated("chest_pain.onset", "started about an hour ago", "PT1H"),
                    Stated("symptom_onset_at", "about an hour ago", "about an hour ago"),
                ),
            ),
            Answer(
                "chest_pain.radiates",
                "Does the pain spread to your arm, jaw, neck or back?",
                "Yes, it goes down my left arm.",
                (Stated("chest_pain.radiates", "it goes down my left arm", "yes"),),
            ),
            Answer(
                "chest_pain.sweating",
                "Are you sweating with the pain?",
                "Yes, I'm sweating a lot.",
                (Stated("chest_pain.sweating", "I'm sweating a lot", "yes"),),
            ),
            Answer(
                "chest_pain.short_of_breath",
                "Are you short of breath?",
                "A little, yes.",
                (Stated("chest_pain.short_of_breath", "A little, yes", "yes"),),
            ),
            Answer(
                "pain_score",
                "How bad is the pain now, from 0 to 10?",
                "It's an 8.",
                (Stated("pain_score", "an 8", 8),),
            ),
            Answer(
                "allergies_confirm",
                "Do you have any allergies to medicines?",
                "No, no allergies.",
                (Stated("allergies_confirm", "no allergies", "no allergies"),),
            ),
        ),
        device={"heart_rate": 104, "spo2": 97, "temperature_c": 36.9},
        nurse_vitals={
            "respiratory_rate": 20,
            "systolic_bp": 150,
            "diastolic_bp": 90,
            "acvpu": "A",
            "on_supplemental_o2": "no",
        },
        nurse_facts={"tasks.ecg_done_at": "2026-09-27T09:07:00+00:00"},
        decide=at_most(2, "Chest pain alert with radiation and sweating."),
    ),
    Persona(
        id="stroke",
        description="Face droop, right arm weakness and slurred speech; last known well 65 "
        "minutes before arrival; the nurse sets level 1.",
        arrived_at=at(9, 20),
        answers=(
            Answer(
                "chief_complaint",
                "What brings you in today?",
                "My face feels droopy and I can't lift my right arm properly.",
                (
                    Stated(
                        "chief_complaint",
                        "My face feels droopy and I can't lift my right arm properly",
                        "face feels droopy and cannot lift the right arm properly",
                    ),
                    Stated("fast.face_droop", "My face feels droopy", "yes"),
                    Stated("fast.arm_weakness", "I can't lift my right arm properly", "yes"),
                ),
            ),
            Answer(
                "fast.speech_difficulty",
                "Is your speech slurred, or are words hard to get out?",
                "Yes, my words come out wrong.",
                (Stated("fast.speech_difficulty", "my words come out wrong", "yes"),),
            ),
            Answer(
                "fast.last_known_well",
                "When did you last feel completely normal?",
                "At a quarter past eight this morning.",
                (
                    Stated(
                        "fast.last_known_well",
                        "a quarter past eight this morning",
                        "2026-09-27T08:15:00+00:00",
                    ),
                ),
            ),
            Answer(
                "allergies_confirm",
                "Do you have any allergies to medicines?",
                "I'm allergic to penicillin.",
                (Stated("allergies_confirm", "allergic to penicillin", "allergic to penicillin"),),
            ),
        ),
        device={"heart_rate": 88, "spo2": 96, "temperature_c": 36.7},
        nurse_vitals={
            "respiratory_rate": 18,
            "systolic_bp": 182,
            "diastolic_bp": 96,
            "acvpu": "A",
            "on_supplemental_o2": "no",
        },
        decide=at_most(1, "FAST positive; stroke team called."),
    ),
    Persona(
        id="child-drowsy",
        description="A 2-year-old, hard to wake since this morning, fever last night; the parent "
        "answers. No model suggestion (under 15); NEWS2 does not apply.",
        arrived_at=at(9, 40),
        answers=(
            Answer(
                "chief_complaint",
                "What brings you in today?",
                "He has been very hard to wake since this morning and he had a fever last night.",
                (
                    Stated(
                        "chief_complaint",
                        "very hard to wake since this morning",
                        "very hard to wake since this morning",
                    ),
                    Stated("child_danger.hard_to_wake", "very hard to wake", "yes"),
                    Stated("infection.fever_reported", "he had a fever last night", "yes"),
                    Stated("symptom_onset_at", "since this morning", "since this morning"),
                ),
            ),
            Answer(
                "child_danger.non_blanching_rash",
                "Does he have a rash that does not fade when you press a glass on it?",
                "No, no rash.",
                (Stated("child_danger.non_blanching_rash", "no rash", "no"),),
            ),
            Answer(
                "child_danger.seizure",
                "Has he had a fit or seizure?",
                "No.",
                (Stated("child_danger.seizure", "No", "no"),),
            ),
            Answer(
                "allergies_confirm",
                "Do you know of any allergies to medicines?",
                "Not that I know of.",
                (Stated("allergies_confirm", "Not that I know of", "none known"),),
            ),
        ),
        device={"heart_rate": 150, "spo2": 95, "temperature_c": 38.9},
        decide=lambda s: ("OVERRIDE", 2, "other", "no model suggestion"),
    ),
    Persona(
        id="sepsis",
        description="Fever, rigors and productive cough; kiosk vitals give a partial NEWS2, then "
        "the nurse completes it and records a suspected infection.",
        arrived_at=at(10, 0),
        answers=(
            Answer(
                "chief_complaint",
                "What brings you in today?",
                "I've had a fever and shivering for two days, and I'm coughing up green phlegm.",
                (
                    Stated(
                        "chief_complaint",
                        "a fever and shivering for two days",
                        "fever and shivering for two days",
                    ),
                    Stated("infection.fever_reported", "a fever", "yes"),
                    Stated("infection.rigors", "shivering", "yes"),
                    Stated("symptoms", "coughing up green phlegm", "productive_cough"),
                    Stated("symptom_onset_at", "for two days", "for two days"),
                ),
            ),
            Answer(
                "allergies_confirm",
                "Do you have any allergies to medicines?",
                "No allergies.",
                (Stated("allergies_confirm", "No allergies", "no allergies"),),
            ),
        ),
        device={"heart_rate": 125, "spo2": 93, "temperature_c": 39.4},
        nurse_vitals={
            "respiratory_rate": 24,
            "systolic_bp": 98,
            "diastolic_bp": 60,
            "acvpu": "A",
            "on_supplemental_o2": "no",
        },
        nurse_facts={"infection.suspected": "yes"},
    ),
    Persona(
        id="routine",
        description="Routine walk-in with an appointment: a sore throat for five days, getting "
        "better; kiosk vitals only (partial NEWS2).",
        arrived_at=at(10, 20),
        answers=(
            Answer(
                "chief_complaint",
                "What brings you in today?",
                "I've had a sore throat for five days, but it's getting better.",
                (
                    Stated("chief_complaint", "sore throat", "sore throat"),
                    Stated("symptom_onset_at", "for five days", "for five days"),
                ),
            ),
            Answer(
                "pain_score",
                "How bad is the pain now, from 0 to 10?",
                "About a 3.",
                (Stated("pain_score", "a 3", 3),),
            ),
            Answer(
                "allergies_confirm",
                "Do you have any allergies to medicines?",
                "I'm allergic to penicillin.",
                (Stated("allergies_confirm", "allergic to penicillin", "allergic to penicillin"),),
            ),
        ),
        device={"heart_rate": 78, "spo2": 99, "temperature_c": 37.1},
    ),
    Persona(
        id="qr-phone",
        description="Continues on the phone after the QR hand-over: no kiosk vitals, one "
        "extraction failed twice, two statements dropped, and the intake hit its limit.",
        arrived_at=at(10, 40),
        answers=(
            Answer(
                "chief_complaint",
                "What brings you in today?",
                "I twisted my ankle playing football this morning.",
                (
                    Stated("chief_complaint", "twisted my ankle", "twisted my ankle"),
                    Stated("injury", "twisted my ankle", "yes"),
                    Stated("symptom_onset_at", "this morning", "this morning"),
                ),
            ),
            Answer(
                "pain_score",
                "How bad is the pain now, from 0 to 10?",
                "Maybe a 6.",
                (Stated("pain_score", "a 6", 6),),
            ),
        ),
        flags={"extraction_incomplete": True, "intake_truncated": True},
        dropped=2,
    ),
    Persona(
        id="no-show",
        description="Appointment today; the EHR could not be read at check-in, so the allergy "
        "question is asked fresh. (The no-show happens later, in the queue.)",
        arrived_at=at(11, 0),
        answers=(
            Answer(
                "chief_complaint",
                "What brings you in today?",
                "I have a bad headache since yesterday evening.",
                (
                    Stated("chief_complaint", "bad headache", "bad headache"),
                    Stated(
                        "symptom_onset_at", "since yesterday evening", "since yesterday evening"
                    ),
                ),
            ),
            Answer(
                "pain_score",
                "How bad is the pain now, from 0 to 10?",
                "It's a 7.",
                (Stated("pain_score", "a 7", 7),),
            ),
            Answer(
                "allergies_confirm",
                "Do you have any allergies to medicines?",
                "No, none that I know of.",
                (Stated("allergies_confirm", "none that I know of", "none known"),),
            ),
        ),
        device={"heart_rate": 82, "spo2": 98, "temperature_c": 36.8},
        ehr=EhrStatus.unavailable,
        flags={"ehr_unavailable": True},
        decide=lambda s: ("OVERRIDE", 3, "clinical_judgement", None),
    ),
)


def entry(key: str, fact: dict[str, Any]) -> FactEntry:
    if key == "symptoms":
        return FactEntry.model_validate({"key": key, "fact": {"name": fact}})
    return FactEntry.model_validate({"key": key, "fact": fact})


def stated_fact(persona: Persona, turn: Turn, stated: Stated) -> FactEntry:
    start = turn.text.index(stated.quote)
    source = {
        "kind": "transcript_span",
        "turn_id": turn.turn_id,
        "start": start,
        "end": start + len(stated.quote),
        "quote": stated.quote,
    }
    return entry(
        stated.key,
        {
            "fact_id": stable_fact_id(persona.session_id, stated.key, turn.turn_id),
            "provenance": "PATIENT_STATED",
            "source": source,
            "recorded_at": turn.created_at,
            "value": stated.value,
        },
    )


def vital(
    persona: Persona, name: str, value: Any, source: dict[str, Any], when: datetime
) -> FactEntry:
    provenance = "DEVICE_MEASURED" if source["kind"] == "device" else "NURSE_CONFIRMED"
    body: dict[str, Any] = {
        "fact_id": stable_fact_id(persona.session_id, f"vitals.{name}", provenance),
        "provenance": provenance,
        "source": source,
        "recorded_at": when,
    }
    if name == "acvpu":
        body["value"] = {"system": ACVPU_SYSTEM, "code": value, "display": ACVPU_DISPLAY[value]}
    elif name == "on_supplemental_o2":
        body["value"] = value
    else:
        body |= {"value": value, "unit": UNITS[name]}
    return entry(f"vitals.{name}", body)


def ehr_record(
    persona: Persona, when: datetime
) -> tuple[str | None, EhrContext | None, list[FactEntry]]:
    if persona.ehr is not EhrStatus.ok:
        return None, None, []
    bundle = json.loads((SEED / f"{persona.id}.json").read_text(encoding="utf-8"))
    resources: dict[str, list[dict[str, Any]]] = {}
    for item in bundle["entry"]:
        resource = {**item["resource"], "meta": {"versionId": "1"}}
        resources.setdefault(resource["resourceType"], []).append(resource)
    adapter = load_adapter()
    patient = resources["Patient"][0]
    demographics = mapper.patient(patient, when)
    context = EhrContext(
        allergies=mapper.allergies(resources.get("AllergyIntolerance", []), adapter, when),
        conditions=mapper.conditions(resources.get("Condition", []), adapter, when),
        medications=mapper.medications(resources.get("MedicationStatement", []), adapter, when),
    )
    found = [
        FactEntry(key=key, fact=fact)
        for key, fact in (
            (FactKey.patient_age_years, demographics.age_years),
            (FactKey.patient_age_months, demographics.age_months),
            (FactKey.patient_sex, demographics.sex),
        )
        if fact is not None
    ]
    return f"Patient/{patient['id']}", context, found


def run_rules(state: IntakeState, now: datetime) -> None:
    result = evaluate(structured_facts(state), now, arrived_at=state["arrived_at"])
    state["alerts"] = merge_alerts_by_rule_id(state.get("alerts", []), list(result.alerts))
    state["ruleset_version"] = result.ruleset_version


def build(persona: Persona, model: UrgencyModel) -> IntakeState:
    arrived = persona.arrived_at
    state = IntakeState(
        session_id=persona.session_id,
        clinic_id=CLINIC,
        language=Language.en,
        arrived_at=arrived,
    )
    patient_ref, context, demographics = ehr_record(persona, arrived + timedelta(seconds=20))
    state["patient_ref"] = patient_ref
    state["ehr_status"] = persona.ehr
    state["ehr_errors"] = [] if persona.ehr is EhrStatus.ok else ["connect timeout"]
    state["ehr_context"] = context
    state["facts"] = list(demographics)
    state["turns"] = []
    state["alerts"] = []
    clock = arrived + timedelta(minutes=1)
    for answer in persona.answers:
        number = len(state["turns"]) + 1
        asked = Turn(
            turn_id=f"trn_{number:03d}",
            role=TurnRole.assistant,
            text=answer.question,
            question_key=FactKey(answer.question_key),
            created_at=clock,
        )
        clock += timedelta(seconds=20)
        said = Turn(
            turn_id=f"trn_{number + 1:03d}",
            role=TurnRole.patient,
            text=answer.answer,
            question_key=FactKey(answer.question_key),
            created_at=clock,
        )
        state["turns"] += [asked, said]
        state["facts"] += [stated_fact(persona, said, stated) for stated in answer.facts]
        if answer.question_key == "chief_complaint":
            complaint = next(s for s in answer.facts if s.key == "chief_complaint")
            code, display = complaint_category(complaint.value)
            state["facts"].append(
                entry(
                    "complaint_category",
                    {
                        "fact_id": stable_fact_id(persona.session_id, "complaint_category"),
                        "provenance": "MODEL_INFERRED",
                        "source": KEYWORD_MODEL,
                        "recorded_at": clock,
                        "value": {"system": COMPLAINT_SYSTEM, "code": code, "display": display},
                    },
                )
            )
        run_rules(state, clock)
        clock += timedelta(seconds=10)
    device_at = arrived + timedelta(minutes=3)
    if persona.device:
        reading = {
            "kind": "device",
            "device_id": DEVICE,
            "reading_id": f"rd_{persona.session_id}_01",
        }
        state["facts"] += [
            vital(persona, n, v, reading, device_at) for n, v in persona.device.items()
        ]
        run_rules(state, device_at)
    nurse_at = arrived + timedelta(minutes=6)
    nurse = {"kind": "nurse", "staff_user_id": NURSE}
    if persona.nurse_vitals:
        state["facts"] += [
            vital(persona, n, v, nurse, nurse_at) for n, v in persona.nurse_vitals.items()
        ]
    for key, value in persona.nurse_facts.items():
        when = datetime.fromisoformat(value) if key == "tasks.ecg_done_at" else nurse_at
        state["facts"].append(
            entry(
                key,
                {
                    "fact_id": stable_fact_id(persona.session_id, key, "nurse"),
                    "provenance": "NURSE_CONFIRMED",
                    "source": nurse,
                    "recorded_at": when,
                    "value": value,
                },
            )
        )
    if persona.nurse_vitals or persona.nurse_facts:
        run_rules(state, nurse_at)
    state["extraction"] = {
        "dropped_count": persona.dropped,
        "model_id": EXTRACTION_MODEL,
        "prompt_version": EXTRACTION_PROMPT,
    }
    state["flags"] = dict(persona.flags)  # type: ignore[typeddict-item]
    suggest_at = arrived + timedelta(minutes=7)
    facts = structured_facts(state)
    try:
        suggestion: UrgencySuggestion | None = model.predict(
            facts, scale=TriageScale.KTAS, now=suggest_at
        )
        state["suggestion_unavailable"] = None
    except OutOfScopeError as exc:
        suggestion = None
        state["suggestion_unavailable"] = str(exc)
    if suggestion is not None and (problems := check_suggestion_evidence(suggestion, facts)):
        raise ValueError(f"{persona.id}: {problems}")
    state["suggestion"] = suggestion
    action, level, reason, note = persona.decide(suggestion)
    decision: dict[str, Any] = {
        "decision_id": f"dec_{persona.session_id}",
        "session_id": persona.session_id,
        "suggestion_id": suggestion.suggestion_id if suggestion else None,
        "action": action,
        "final_level": level,
        "scale": "KTAS",
        "nurse_user_id": NURSE,
        "acknowledged_alert_ids": [alert.alert_id for alert in state["alerts"]],
        "decided_at": arrived + timedelta(minutes=8),
    }
    if reason is not None:
        decision["override_reason_code"] = reason
    if note is not None:
        decision["override_note"] = note
    state["nurse_decision"] = NurseDecision.model_validate(decision)
    return state


def main() -> None:
    model = UrgencyModel.load(MODEL_ARTIFACT)
    for persona in PERSONAS:
        state = build(persona, model)
        body = {
            "persona": persona.id,
            "description": persona.description,
            "generated_by": "services/intake/tests/fixtures/briefing/generate.py",
            "ruleset": RULESET_VERSION,
            "urgency_model": model.model_version,
            "state": state_to_json(state),
        }
        path = FIXTURES / f"{persona.id}.json"
        path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        alerts = [alert.rule_id.value for alert in state.get("alerts", [])]
        suggestion = state.get("suggestion")
        level = f"KTAS {int(suggestion.level)}" if suggestion else "none"
        print(f"{persona.id}: alerts={alerts} suggestion={level} -> {path.name}")


if __name__ == "__main__":
    main()
