"""Layer A of the medical-question classifier (M2-09): the lexicon in `medical_lexicon.yaml`.

English entries are regular expressions matched as whole words, case-insensitively. Arabic is
matched word by word after normalisation (no diacritics or tatweel; alef, alef maqsura, ta marbuta
and hamza seats folded), with one common prefix and one common suffix removed, because Arabic
attaches the article, conjunctions, prepositions and pronouns to the word. A substring match would
be wrong: the normalised "ألم" (pain) is inside "الماء" (the water). Both languages are checked
for every question, whatever the session language.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any, Final

import yaml

_AR_MARKS: Final = re.compile("[ؗ-ًؚ-ٰٟـ]")
_AR_WORD: Final = re.compile("[ء-ي]+")
_AR_FOLD: Final = str.maketrans(
    {"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي"}
)
AR_PREFIXES: Final = ("وال", "فال", "بال", "كال", "لل", "ال", "و", "ف", "ب", "ك", "ل")
AR_SUFFIXES: Final = (
    "هما", "ها", "هم", "هن", "كم", "كن", "نا", "ني", "ات", "ين", "ون", "ان", "يه", "ه", "ي", "ك"
)  # fmt: skip
MIN_STEM: Final = 2


def normalise_ar(text: str) -> str:
    return _AR_MARKS.sub("", text).translate(_AR_FOLD)


def ar_words(text: str) -> list[str]:
    return _AR_WORD.findall(normalise_ar(text))


def ar_stems(word: str) -> set[str]:
    """The word, and the word without one common prefix, one common suffix, or both."""
    heads = {word} | {
        word[len(p) :] for p in AR_PREFIXES if word.startswith(p) and len(word) - len(p) >= MIN_STEM
    }
    stems = set(heads)
    for head in heads:
        stems |= {
            head[: -len(s)]
            for s in AR_SUFFIXES
            if head.endswith(s) and len(head) - len(s) >= MIN_STEM
        }
    return stems


@dataclass(frozen=True)
class Lexicon:
    english: re.Pattern[str]
    arabic_words: frozenset[str]
    arabic_phrases: tuple[str, ...]

    def match(self, question: str) -> str | None:
        """The first entry the question contains, or None. For logs and tests: never log the
        question itself."""
        lowered = " ".join(question.lower().replace("’", "'").split())
        if (found := self.english.search(lowered)) is not None:
            return found.group(0)
        words = ar_words(question)
        for word in words:
            if hits := ar_stems(word) & self.arabic_words:
                return min(hits)
        joined = f" {' '.join(words)} "
        return next((phrase for phrase in self.arabic_phrases if f" {phrase} " in joined), None)


def parse_lexicon(body: dict[str, Any]) -> Lexicon:
    english = [pattern for group in body["en"].values() for pattern in group]
    arabic = body["ar"]
    return Lexicon(
        english=re.compile(r"\b(?:" + "|".join(f"(?:{p})" for p in english) + r")\b", re.I),
        arabic_words=frozenset(normalise_ar(word) for word in arabic["words"]),
        arabic_phrases=tuple(" ".join(ar_words(phrase)) for phrase in arabic["phrases"]),
    )


@cache
def load_lexicon() -> Lexicon:
    text = (resources.files("careflow_rag") / "medical_lexicon.yaml").read_text(encoding="utf-8")
    return parse_lexicon(yaml.safe_load(text))


__all__ = ["Lexicon", "ar_stems", "ar_words", "load_lexicon", "normalise_ar", "parse_lexicon"]
