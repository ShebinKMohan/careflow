""" "I feel worse" on the phone (M2-07): the three danger answers as codes, turns and facts.

Ticked items only become facts; an unticked item is not a "no", because the patient did not say
no. The ticked chest items become `chest_pain.present` / `chest_pain.short_of_breath` = yes, and
the ticked FAST items `fast.face_droop` / `arm_weakness` / `speech_difficulty` = yes, each a
PATIENT_STATED fact citing the press's turn (safety rule 4). "Drowsy or confused" becomes
`child_danger.hard_to_wake` for a child only: yes, or unknown for "not sure" (a parent's unsure red
answer counts as red, M2-04). For an adult it stays on the nurse's card: no fact is invented.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Final

from careflow_contracts.generated.structured_facts_schema import (
    FactKey,
    Language,
    Turn,
    TurnRole,
)
from careflow_intake.facts import FactEntry, stable_fact_id

CHEST: Final = {
    "chest_pain": FactKey.chest_pain_present,
    "short_of_breath": FactKey.chest_pain_short_of_breath,
}
FAST: Final = {
    "face_droop": FactKey.fast_face_droop,
    "arm_weakness": FactKey.fast_arm_weakness,
    "speech_difficulty": FactKey.fast_speech_difficulty,
}
DROWSY: Final = "drowsy_or_confused"
DROWSY_UNSURE: Final = "drowsy_or_confused_unsure"

_TEXTS: Final = {
    Language.en: {
        "ask": "What has changed?",
        "said": "I feel worse",
        "chest_pain": "chest pain",
        "short_of_breath": "short of breath",
        "face_droop": "face drooping",
        "arm_weakness": "arm weak",
        "speech_difficulty": "speech difficult",
        DROWSY: "drowsy or confused",
        DROWSY_UNSURE: "not sure if drowsy or confused",
    },
    Language.ar: {
        "ask": "ما الذي تغيّر؟",
        "said": "أشعر أنني أسوأ",
        "chest_pain": "ألم في الصدر",
        "short_of_breath": "ضيق في التنفس",
        "face_droop": "ارتخاء في الوجه",
        "arm_weakness": "ضعف في الذراع",
        "speech_difficulty": "صعوبة في الكلام",
        DROWSY: "نعاس أو ارتباك",
        DROWSY_UNSURE: "لست متأكداً من النعاس أو الارتباك",
    },
}


def answer_codes(
    chest_or_breathing: Iterable[str], face_arm_speech: Iterable[str], drowsy_or_confused: str
) -> list[str]:
    """The press's answers as codes, in a fixed order; never free text."""
    chest, fast = set(chest_or_breathing), set(face_arm_speech)
    codes = [code for code in CHEST if code in chest] + [code for code in FAST if code in fast]
    if drowsy_or_confused == "yes":
        codes.append(DROWSY)
    elif drowsy_or_confused == "not_sure":
        codes.append(DROWSY_UNSURE)
    return codes


def _fact(turn: Turn, key: FactKey, value: str) -> FactEntry:
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
                    "quote": turn.text[:1000],
                },
                "recorded_at": turn.created_at,
                "value": value,
            },
        }
    )


def feels_worse_turns_and_facts(
    *,
    language: Language | str,
    turn_number: int,
    patient_type: str,
    codes: Sequence[str],
    now: datetime,
) -> tuple[list[Turn], list[FactEntry]]:
    """The press as two turns (the question, and the patient's ticked answers in the session
    language) and the facts they state."""
    texts = _TEXTS[Language(language)]
    said = texts["said"] + (": " + "; ".join(texts[code] for code in codes) if codes else ".")
    ask = Turn(
        turn_id=f"trn_{turn_number:03d}",
        role=TurnRole.assistant,
        text=texts["ask"],
        created_at=now,
    )
    answer = Turn(
        turn_id=f"trn_{turn_number + 1:03d}",
        role=TurnRole.patient,
        text=said,
        created_at=now,
    )
    facts = [_fact(answer, key, "yes") for code, key in (CHEST | FAST).items() if code in codes]
    if patient_type == "child":
        if DROWSY in codes:
            facts.append(_fact(answer, FactKey.child_danger_hard_to_wake, "yes"))
        elif DROWSY_UNSURE in codes:
            facts.append(_fact(answer, FactKey.child_danger_hard_to_wake, "unknown"))
    return [ask, answer], facts


__all__ = ["DROWSY", "DROWSY_UNSURE", "answer_codes", "feels_worse_turns_and_facts"]
