"""The patient-output safety gate (M2-09, SHE-306): the last check before a model's answer reaches
a patient.

It is independent of the model that wrote the answer: plain rules, run on every generated answer
after its citations were checked. A valid citation does not make an answer safe: a model can cite
the pharmacy section and still add a dose. The gate blocks, in English and Arabic:

- **a dose or amount of medicine** (a number with mg, ml, tablets, puffs, and so on);
- **a diagnosis** or a statement about what the patient has;
- **reassurance or triage advice** ("don't worry", "nothing serious", "you can go home", "it can
  wait", "no need to see a doctor");
- **a medical term the sources do not contain**: the question classifier's lexicon and the
  condition names, found in the answer but not in the cited handbook text. The handbook is
  practical and reviewed (M2-08, ADR-053), so a medical word the model adds on its own did not
  come from the sources ("Bring a list of the medicines you take" is grounded; "paracetamol" is
  not).

A blocked answer is never shown. The agent then refuses and flags the nurse, as for a medical
question (category `output_gate`): when unsure, refuse and flag (safety rule 2). A regex gate is
a backstop, not a guarantee; the classifier, the prompt and the citations come first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

from careflow_rag.lexicon import Lexicon, load_lexicon, normalise_ar

_I: Final = re.IGNORECASE
_DIGITS: Final = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

DOSE: Final = re.compile(
    r"\b\d+(?:[.,]\d+)?\s?(?:mg|mcg|µg|g|ml|mls|units?|tablets?|tabs?|pills?|capsules?|puffs?|drops?|doses?)\b",
    _I,
)
# The Arabic patterns are written in `normalise_ar`'s form (أ إ آ -> ا, ى -> ي, ة -> ه): "على"
# is matched as "علي".
AR_DOSE: Final = re.compile(
    r"\d+(?:[.,]\d+)?\s?(?:ملغ|مغ|ملغم|غرام|مل|وحده|وحدات|حبه|حبات|حبوب|اقراص|قرص|كبسوله|كبسولات|نقط|جرعه|جرعات|بخه|بخات)"
)
DIAGNOSIS: Final = (
    re.compile(r"\byou\s+(?:probably|likely|may|might|could|must)\s+(?:have|be\s+having)\b", _I),
    re.compile(r"\b(?:it|this|that)\s+(?:sounds|looks|seems)\s+like\b", _I),
    re.compile(r"\bdiagnos", _I),
    re.compile(r"\byou(?:['’]re|\s+are)\s+(?:having|suffering)\b", _I),
)
AR_DIAGNOSIS: Final = (
    re.compile(r"تشخيص"),
    re.compile(r"(?:علي\s+الارجح|غالبا|ربما|قد\s+تكون|يمكن\s+ان\s+تكون)\s+(?:لديك|عندك|مصاب)"),
    re.compile(r"(?:انت|انتي)\s+مصاب"),
    re.compile(r"(?:يبدو|يظهر)\s+انك"),
)
REASSURANCE: Final = (
    re.compile(r"\bdon['’]?t\s+worry\b", _I),
    re.compile(r"\bnothing\s+(?:serious|to\s+worry)\b", _I),
    re.compile(r"\b(?:not|isn['’]t|is\s+not)\s+(?:serious|dangerous|an\s+emergency)\b", _I),
    re.compile(r"\b(?:go|stay)\s+home\b", _I),
    re.compile(r"\b(?:it|this)\s+can\s+wait\b", _I),
    re.compile(r"\bno\s+need\s+to\s+(?:see|wait|worry)\b", _I),
    # Medicine advice. "You can take the prescription to any pharmacy" (the handbook) is not.
    re.compile(
        r"\b(?:take|stop\s+taking|keep\s+taking)\s+(?:your|the|a|an|some|this|these|that)?\s*"
        r"(?:medicines?|medications?|tablets?|pills?|drugs?|antibiotics?|painkillers?|doses?)\b",
        _I,
    ),
    re.compile(r"\bsafe\s+to\s+(?:take|drive|eat|drink|stop)\b", _I),
)
AR_REASSURANCE: Final = (
    re.compile(r"لا\s+(?:تقلق|داعي\s+للقلق)"),
    re.compile(r"(?:ليس|مش|مو)\s+(?:شيئا\s+|شي\s+|شيء\s+)?(?:خطير|خطيرا|طارئا)"),
    re.compile(
        r"(?:اذهب|روح|عد|عودي|ارجع|تذهب|تروح|تعود|ترجع|الذهاب|العوده|الرجوع)\S*\s+(?:الي\s+|علي\s+)?(?:البيت|المنزل|بيتك|منزلك)"
    ),
    re.compile(r"يمكن\S*\s+(?:ان\s+)?(?:ينتظر|تنتظر)"),
    re.compile(r"(?:يمكنك|تستطيع|من\s+الامن)\s+(?:ان\s+)?(?:تاخذ|تتناول|تقود|توقف)"),
    re.compile(r"(?:خذ|تناول)\s+(?:الدواء|دواء|حبه|حبتين|حبات|حبوب|الحبوب|قرص|قرصين|اقراص|كبسوله)"),
)


@dataclass(frozen=True)
class GateResult:
    allowed: bool
    reasons: list[str] = field(default_factory=list)


def check_answer(answer: str, sources: str, *, lexicon: Lexicon | None = None) -> GateResult:
    """Whether a generated answer may reach the patient. `sources` is the text of the chunks and
    tool results the answer was allowed to use; a medical term found only in the answer is not
    grounded in them."""
    lexicon = lexicon or load_lexicon()
    reasons: list[str] = []
    text = answer.translate(_DIGITS)
    ar = normalise_ar(text)
    if DOSE.search(text) or AR_DOSE.search(ar):
        reasons.append("dose")
    if any(p.search(text) for p in DIAGNOSIS) or any(p.search(ar) for p in AR_DIAGNOSIS):
        reasons.append("diagnosis")
    if any(p.search(text) for p in REASSURANCE) or any(p.search(ar) for p in AR_REASSURANCE):
        reasons.append("advice")
    unsupported = lexicon.entries(answer) - lexicon.entries(sources)
    if unsupported:
        reasons.append("medical term not in the sources: " + ", ".join(sorted(unsupported)))
    return GateResult(allowed=not reasons, reasons=reasons)


__all__ = ["GateResult", "check_answer"]
