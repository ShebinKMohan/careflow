"""The kiosk's "Who is the patient?" answer as transcript turns and facts (M2-04).

A parent checking in a child answers two kiosk screens: who the patient is, and the child's age
(years, and months under 2). They become turns in the session language, so the age facts cite the
parent's own answer (PATIENT_STATED, safety rule 4). A stated child age replaces an EHR age: the
record the kiosk found may be the parent's. `patient.age_months` is the whole age in months: exact
under 2, and the stated year's first month from 2 on, which falls in the same NICE NG143 age band
as any month of that year (the bands change at 3, 6, 12, 24 and 60 months).
"""

from __future__ import annotations

from datetime import datetime
from typing import Final, Literal

from careflow_contracts.generated.structured_facts_schema import (
    FactKey,
    Language,
    Turn,
    TurnRole,
)
from careflow_intake.facts import FactEntry, stable_fact_id

type PatientType = Literal["adult", "child"]
AGE_KEYS: Final = frozenset({FactKey.patient_age_years, FactKey.patient_age_months})

_TEXTS: Final = {
    Language.en: {
        "who": "Who is the patient?",
        "adult": "Me",
        "child": "My child",
        "how_old": "How old is your child?",
    },
    Language.ar: {
        "who": "من هو المريض؟",
        "adult": "أنا",
        "child": "طفلي",
        "how_old": "كم عمر طفلك؟",
    },
}


def age_text(language: Language, years: int, months: int) -> str:
    """The stated age in words, as the parent entered it."""
    if language is Language.ar:
        if years == 0:
            return f"{months} شهر"
        if years == 1:
            return f"سنة و{months} شهر" if months else "سنة واحدة"
        return f"{years} سنوات" if 3 <= years <= 10 else f"{years} سنة"
    if years == 0:
        return f"{months} month" + ("" if months == 1 else "s")
    if years == 1:
        return f"1 year {months} month" + ("" if months == 1 else "s") if months else "1 year"
    return f"{years} years"


def _fact(turn: Turn, key: FactKey, value: int) -> FactEntry:
    return FactEntry.model_validate(
        {
            "key": key,
            "fact": {
                "fact_id": stable_fact_id(turn.turn_id, turn.created_at.isoformat(), key.value),
                "provenance": "PATIENT_STATED",
                "source": {
                    "kind": "transcript_span",
                    "turn_id": turn.turn_id,
                    "start": 0,
                    "end": len(turn.text),
                    "quote": turn.text,
                },
                "recorded_at": turn.created_at,
                "value": value,
            },
        }
    )


def patient_turns_and_facts(
    *,
    language: Language | str,
    turn_number: int,
    patient_type: PatientType,
    age_years: int | None,
    age_months: int | None,
    now: datetime,
) -> tuple[list[Turn], list[FactEntry]]:
    """The turns (numbered from `turn_number`) and, for a child, the stated age facts."""
    language = Language(language)
    texts = _TEXTS[language]

    def turn(offset: int, role: TurnRole, text: str, key: FactKey | None = None) -> Turn:
        turn_id = f"trn_{turn_number + offset:03d}"
        return Turn(turn_id=turn_id, role=role, text=text, question_key=key, created_at=now)

    turns = [
        turn(0, TurnRole.assistant, texts["who"]),
        turn(1, TurnRole.patient, texts[patient_type]),
    ]
    if patient_type != "child" or age_years is None:
        return turns, []
    months = age_months or 0
    said = turn(
        3, TurnRole.patient, age_text(language, age_years, months), FactKey.patient_age_years
    )
    turns += [turn(2, TurnRole.assistant, texts["how_old"], FactKey.patient_age_years), said]
    facts = [
        _fact(said, FactKey.patient_age_years, age_years),
        _fact(said, FactKey.patient_age_months, age_years * 12 + months),
    ]
    return turns, facts


__all__ = ["AGE_KEYS", "PatientType", "age_text", "patient_turns_and_facts"]
