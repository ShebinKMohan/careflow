"""Arabic text helpers for the intake (M2-03): the normalisation spans and numbers are matched with,
Arabic-Indic digits, number words and yes/no words.

Normalisation drops tatweel (U+0640) and the diacritics (U+064B-U+0652, U+0670), maps the hamza and
madda alef forms أ إ آ to ا, and composes a letter with a following combining mark (NFC), so a quote
typed or transcribed with or without them still matches the patient's turn. It is applied
character by character; `verify.py` keeps the map back to the original offsets.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

TATWEEL: Final = "ـ"
DIACRITICS: Final = frozenset(chr(code) for code in range(0x064B, 0x0653)) | {"ٰ"}
ALEF_FORMS: Final = {"أ": "ا", "إ": "ا", "آ": "ا"}  # أ إ آ -> ا
# Arabic-Indic (٠-٩) and Eastern Arabic-Indic (۰-۹) digits, and the Arabic decimal separator.
DIGITS: Final = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫", "01234567890123456789.")


def is_dropped(char: str) -> bool:
    """Tatweel and diacritics: not part of the normalised text."""
    return char == TATWEEL or char in DIACRITICS


def fold(char: str) -> str:
    """One character, normalised: alef forms to ا, then casefolded (English is unchanged)."""
    return ALEF_FORMS.get(char, char).casefold()


def normalise(text: str) -> str:
    """The normalised text, without an offset map (for words and short values)."""
    out: list[str] = []
    for char in text:
        if is_dropped(char):
            continue
        if unicodedata.combining(char) and out:
            composed = unicodedata.normalize("NFC", out[-1] + char)
            if len(composed) == 1:
                out[-1] = fold(composed)
            continue
        out.append(fold(char))
    return "".join(out)


def _forms(*words: str) -> frozenset[str]:
    """Each word as normalised, and with a final ة written ه (a common informal spelling)."""
    base = {normalise(word) for word in words}
    return frozenset(base | {word[:-1] + "ه" for word in base if word.endswith("ة")})


# 0 to 10, in Modern Standard Arabic and the Gulf and Levantine spellings a patient may use.
NUMBER_WORDS: Final[dict[int, frozenset[str]]] = {
    0: _forms("صفر"),
    1: _forms("واحد", "واحدة"),
    2: _forms("اثنان", "اثنين", "اثنتان", "اثنتين", "ثنين", "اتنين", "تنين"),
    3: _forms("ثلاثة", "ثلاث", "تلاتة", "تلات"),
    4: _forms("أربعة", "أربع"),
    5: _forms("خمسة", "خمس"),
    6: _forms("ستة", "ست"),
    7: _forms("سبعة", "سبع"),
    8: _forms("ثمانية", "ثماني", "ثمان", "تمانية", "تماني", "تمنية"),
    9: _forms("تسعة", "تسع"),
    10: _forms("عشرة", "عشر"),
}
_WORD: Final = re.compile(r"\w+")

YES: Final = _forms("نعم", "إي", "إيه", "أيوه", "أيوا", "أجل", "بلى")
NO: Final = _forms("لا", "لأ", "كلا")
UNSURE: Final = _forms(
    "لا أعرف", "ما أعرف", "ما بعرف", "مش عارف", "ما أدري", "مدري", "مش متأكد", "مو متأكد"
)


def number_word_in(number: float, text: str) -> bool:
    """An Arabic word for `number` (0 to 10) is one of the words of `text`, alone or after و."""
    words = NUMBER_WORDS.get(int(number)) if number.is_integer() else None
    if not words:
        return False
    for token in _WORD.findall(normalise(text)):
        if token in words or (token.startswith("و") and token[1:] in words):
            return True
    return False


__all__ = [
    "DIGITS",
    "NO",
    "NUMBER_WORDS",
    "UNSURE",
    "YES",
    "fold",
    "is_dropped",
    "normalise",
    "number_word_in",
]
