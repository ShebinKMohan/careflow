"""Layer A of the medical-question classifier (M2-09): the lexicon in `medical_lexicon.yaml`.

English entries are regular expressions matched as whole words, case-insensitively. Arabic is
matched word by word after normalisation (no diacritics or tatweel; alef, alef maqsura, ta marbuta
and hamza seats folded), with one common prefix and one common suffix removed, because Arabic
attaches the article, conjunctions, prepositions and pronouns to the word. A substring match would
be wrong: the normalised "ألم" (pain) is inside "الماء" (the water). A phrase matches a run of
consecutive words, each through the same prefix and suffix removal, so "المضادات الحيوية" matches
the phrase "مضادات حيوية" (SHE-306). Both languages are checked for every question, whatever the
session language.

`entries()` lists every entry a text contains; the ask-the-clinic output gate compares an
answer's entries with its sources' (`careflow_rag.output_gate`).
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
    arabic_phrases: tuple[tuple[str, ...], ...]
    #: Each English entry on its own, by its source pattern (for `entries`).
    english_entries: tuple[tuple[str, re.Pattern[str]], ...] = ()

    def match(self, question: str) -> str | None:
        """The first entry the question contains, or None. For logs and tests: never log the
        question itself."""
        if (found := self.english.search(_lowered(question))) is not None:
            return found.group(0)
        words = ar_words(question)
        for word in words:
            if hits := ar_stems(word) & self.arabic_words:
                return min(hits)
        return next((" ".join(p) for p in self.arabic_phrases if _has_phrase(words, p)), None)

    def entries(self, text: str) -> set[str]:
        """Every entry the text contains: English patterns by their source (so "medicine" and
        "medicines" are the same entry), Arabic words as their lexicon form, and phrases."""
        lowered = _lowered(text)
        found = {source for source, pattern in self.english_entries if pattern.search(lowered)}
        words = ar_words(text)
        for word in words:
            found |= ar_stems(word) & self.arabic_words
        found |= {" ".join(p) for p in self.arabic_phrases if _has_phrase(words, p)}
        return found


def _lowered(text: str) -> str:
    return " ".join(text.lower().replace("’", "'").split())


def _has_phrase(words: list[str], phrase: tuple[str, ...]) -> bool:
    """`phrase` is a run of consecutive `words`, each word matched through `ar_stems`."""
    size = len(phrase)
    return any(
        all(phrase[j] in ar_stems(words[i + j]) for j in range(size))
        for i in range(len(words) - size + 1)
    )


def parse_lexicon(body: dict[str, Any]) -> Lexicon:
    english = [pattern for group in body["en"].values() for pattern in group]
    arabic = body["ar"]
    return Lexicon(
        english=re.compile(r"\b(?:" + "|".join(f"(?:{p})" for p in english) + r")\b", re.I),
        arabic_words=frozenset(normalise_ar(word) for word in arabic["words"]),
        arabic_phrases=tuple(tuple(ar_words(phrase)) for phrase in arabic["phrases"]),
        english_entries=tuple(
            (alt, re.compile(rf"\b(?:{alt})\b", re.I)) for p in english for alt in alternatives(p)
        ),
    )


def alternatives(pattern: str) -> list[str]:
    """The top-level alternatives of a pattern: `medicines?|pills?` is two entries, so an answer
    that says "pills" is not grounded by a source that says "medicines"."""
    parts, depth, start = [], 0, 0
    for index, char in enumerate(pattern):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "|" and depth == 0:
            parts.append(pattern[start:index])
            start = index + 1
    parts.append(pattern[start:])
    return [part for part in parts if part]


@cache
def load_lexicon() -> Lexicon:
    text = (resources.files("careflow_rag") / "medical_lexicon.yaml").read_text(encoding="utf-8")
    return parse_lexicon(yaml.safe_load(text))


__all__ = [
    "Lexicon",
    "alternatives",
    "ar_stems",
    "ar_words",
    "load_lexicon",
    "normalise_ar",
    "parse_lexicon",
]
