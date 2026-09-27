"""The patient-facing output filter (safety rule 2: no AI output reaches a patient as medical
advice). Every string sent to the patient passes through `check_patient_facing`, including the
approved templates. A regex filter is a backstop, not a guarantee: the LLM only rephrases
approved questions.

Arabic (M2-03): the same checks run on the Arabic-normalised text (no diacritics or tatweel,
أ إ آ written ا, Arabic-Indic digits as Western digits), so an Arabic rephrasing that names a
condition, gives advice or a dose, or asks two questions falls back to the approved template.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

from careflow_intake import arabic

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

# Arabic, written as normalised (ا for أ إ آ; ة or ه).
AR_DIAGNOSIS: Final = (
    re.compile(r"تشخيص"),
    re.compile(r"(?:على\s+الارجح|غالبا|ربما|قد\s+تكون|يمكن\s+ان\s+تكون)\s+(?:لديك|عندك|مصاب)"),
    re.compile(r"(?:يبدو|يظهر)\s+ان"),
    re.compile(r"(?:من\s+المحتمل|على\s+الارجح)\s+ان"),
    re.compile(r"(?:انت|انتي)\s+مصاب"),
)
AR_CONDITIONS: Final = re.compile(
    r"نوب[ةه]\s+قلبي[ةه]|جلط[ةه]|سكت[ةه]|انتان|تسمم\s+الدم|عدوى|سرطان|ورم|التهاب\s+رئوي|"
    r"ذات\s+الرئ[ةه]|الزائد[ةه]|السحايا|كسر|انصمام|ذبح[ةه]|ارتجاج"
)
AR_ADVICE: Final = (
    re.compile(r"(?:يجب|ينبغي)\s+(?:عليك|ان\s+ت)"),
    re.compile(r"عليك\s+ان"),
    re.compile(r"انصح|اوصي|ننصح"),
    re.compile(r"لا\s+(?:تقلق|داعي\s+للقلق)"),
    re.compile(r"(?:ليس|مش|مو)\s+(?:شيئا\s+|شي\s+|شيء\s+)?خطير"),
    re.compile(r"(?:اذهب|روح|عد|عودي|ارجع)\S*\s+(?:الى\s+|على\s+)?(?:البيت|المنزل|بيتك|منزلك)"),
)
AR_DOSING: Final = re.compile(r"\d+\s?(?:ملغ|مغ|مل|حب[ةه]|حبات|حبوب|اقراص|قرص)")


@dataclass(frozen=True)
class FilterResult:
    allowed: bool
    reasons: list[str] = field(default_factory=list)


def check_patient_facing(text: str) -> FilterResult:
    """Block diagnoses, condition names, advice, dosing, more than one question and long text."""
    reasons: list[str] = []
    ar = arabic.normalise(text.translate(arabic.DIGITS))
    if any(pattern.search(text) for pattern in DIAGNOSIS) or any(
        pattern.search(ar) for pattern in AR_DIAGNOSIS
    ):
        reasons.append("diagnosis")
    if CONDITIONS.search(text) or AR_CONDITIONS.search(ar):
        reasons.append("condition_name")
    if any(pattern.search(text) for pattern in ADVICE) or any(
        pattern.search(ar) for pattern in AR_ADVICE
    ):
        reasons.append("advice")
    if DOSING.search(text) or AR_DOSING.search(ar):
        reasons.append("dosing")
    if text.count("?") + text.count("؟") > 1:
        reasons.append("more_than_one_question")
    if len(text.split()) > MAX_WORDS:
        reasons.append("too_long")
    if not text.strip():
        reasons.append("empty")
    return FilterResult(allowed=not reasons, reasons=reasons)


__all__ = ["MAX_WORDS", "FilterResult", "check_patient_facing"]
