"""The patient-facing output filter (safety rule 2: no AI output reaches a patient as medical
advice). Every string sent to the patient passes through `check_patient_facing`, including the
approved templates. A regex filter is a backstop, not a guarantee: the LLM only rephrases
approved questions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

MAX_WORDS: Final = 40

_FLAGS = re.IGNORECASE
DIAGNOSIS: Final = (
    # A statement about what the patient has. "Do you have ...?" asks, so it needs a hedge word.
    re.compile(r"\byou\s+(?:probably|likely|may|might|could|must)\s+(?:have|be having)\b", _FLAGS),
    re.compile(r"\byou(?:['’]re|\s+are)\s+having\s+an?\b", _FLAGS),
    re.compile(r"\bdiagnos", _FLAGS),
    re.compile(r"\b(?:it|this|that)\s+(?:sounds|looks|seems)\s+like\b", _FLAGS),
)
CONDITIONS: Final = re.compile(
    r"\b(?:heart\s+attack|stroke|sepsis|septic|infections?|infected|cancer|tumou?r|pneumonia|"
    r"appendicitis|meningitis|fractured?|blood\s+clot|embolism|angina|concussion)\b",
    _FLAGS,
)
ADVICE: Final = (
    re.compile(r"\byou\s+should\b", _FLAGS),
    re.compile(r"\bi\s+(?:would\s+)?recommend\b", _FLAGS),
    re.compile(r"\bdon['’]?t\s+worry\b", _FLAGS),
    re.compile(r"\bnothing\s+serious\b", _FLAGS),
    re.compile(r"\bgo\s+home\b", _FLAGS),
)
DOSING: Final = re.compile(r"\d+\s?(?:mg|ml|tablets?)\b", _FLAGS)


@dataclass(frozen=True)
class FilterResult:
    allowed: bool
    reasons: list[str] = field(default_factory=list)


def check_patient_facing(text: str) -> FilterResult:
    """Block diagnoses, condition names, advice, dosing, more than one question and long text."""
    reasons: list[str] = []
    if any(pattern.search(text) for pattern in DIAGNOSIS):
        reasons.append("diagnosis")
    if CONDITIONS.search(text):
        reasons.append("condition_name")
    if any(pattern.search(text) for pattern in ADVICE):
        reasons.append("advice")
    if DOSING.search(text):
        reasons.append("dosing")
    if text.count("?") > 1:
        reasons.append("more_than_one_question")
    if len(text.split()) > MAX_WORDS:
        reasons.append("too_long")
    if not text.strip():
        reasons.append("empty")
    return FilterResult(allowed=not reasons, reasons=reasons)


__all__ = ["MAX_WORDS", "FilterResult", "check_patient_facing"]
