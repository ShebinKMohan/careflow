"""The clinic handbook: sections parsed from Markdown and split into chunks (M2-08).

A section starts with `<!-- section: <id> -->` and its `## ` heading; the same id is used in every
language (`handbook/en`, `handbook/ar`). A section over 350 words is split at paragraph breaks
into chunks 0, 1, ...; each chunk keeps the section's title. The content hash lets the ingest skip
unchanged chunks.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

type Lang = Literal["en", "ar"]
LANGS: Final[tuple[Lang, ...]] = ("en", "ar")
MAX_WORDS: Final = 350
SECTION: Final = re.compile(r"<!--\s*section:\s*([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)\s*-->")
HEADING: Final = re.compile(r"^#{1,6}\s+(.+?)\s*$")


@dataclass(frozen=True, slots=True)
class Section:
    section_id: str
    lang: Lang
    title: str
    paragraphs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Chunk:
    section_id: str
    chunk_no: int
    lang: Lang
    title: str
    body: str

    @property
    def key(self) -> tuple[str, int, Lang]:
        return self.section_id, self.chunk_no, self.lang

    @property
    def sha256(self) -> str:
        text = f"{self.section_id}\n{self.chunk_no}\n{self.lang}\n{self.title}\n{self.body}"
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse(text: str, lang: Lang) -> list[Section]:
    """The sections of one handbook file, in order."""
    marks = list(SECTION.finditer(text))
    sections: list[Section] = []
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        lines = text[mark.end() : end].strip().splitlines()
        title = ""
        while lines and not lines[0].strip():
            lines.pop(0)
        if lines and (heading := HEADING.match(lines[0])):
            title = heading.group(1)
            lines.pop(0)
        paragraphs = tuple(
            " ".join(part.split()) for part in "\n".join(lines).split("\n\n") if part.strip()
        )
        if not title or not paragraphs:
            raise ValueError(f"section {mark.group(1)!r} ({lang}) needs a heading and a body")
        sections.append(Section(mark.group(1), lang, title, paragraphs))
    return sections


def chunks_of(section: Section, max_words: int = MAX_WORDS) -> list[Chunk]:
    """The section as chunks of at most `max_words` words, split at paragraph breaks (a single
    paragraph longer than that stays whole)."""
    groups: list[list[str]] = [[]]
    words = 0
    for paragraph in section.paragraphs:
        count = len(paragraph.split())
        if groups[-1] and words + count > max_words:
            groups.append([])
            words = 0
        groups[-1].append(paragraph)
        words += count
    return [
        Chunk(section.section_id, number, section.lang, section.title, "\n\n".join(group))
        for number, group in enumerate(groups)
    ]


def load(handbook_dir: Path) -> list[Chunk]:
    """Every chunk of the handbook, English then Arabic, file by file."""
    chunks: list[Chunk] = []
    for lang in LANGS:
        for path in sorted((handbook_dir / lang).glob("*.md")):
            for section in parse(path.read_text(encoding="utf-8"), lang):
                chunks.extend(chunks_of(section))
    keys = [chunk.key for chunk in chunks]
    if len(keys) != len(set(keys)):
        raise ValueError("a section id is used twice in one language")
    return chunks


def handbook_version(chunks: list[Chunk]) -> str:
    """A short hash of the whole handbook: it changes with any chunk."""
    digest = hashlib.sha256("".join(chunk.sha256 for chunk in chunks).encode()).hexdigest()
    return digest[:16]


__all__ = [
    "LANGS",
    "MAX_WORDS",
    "Chunk",
    "Lang",
    "Section",
    "chunks_of",
    "handbook_version",
    "load",
    "parse",
]
