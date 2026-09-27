"""The handbook's parsing and chunking (M2-08), and the shipped handbook's structure."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

from careflow_rag.handbook import (
    MAX_WORDS,
    Chunk,
    Section,
    chunks_of,
    handbook_version,
    load,
    parse,
)
from careflow_rag.ingest import DEFAULT_HANDBOOK

OUTLINE: Final = DEFAULT_HANDBOOK / "OUTLINE.md"


def words(n: int, word: str = "word") -> str:
    return " ".join([word] * n)


def test_a_section_has_its_id_title_and_paragraphs() -> None:
    text = (
        "# Hours\n\n<!-- section: hours.opening -->\n## When we are open\n\n"
        "Open from 7:00.\nEvery   day.\n\nClosed never.\n\n"
        "<!-- section: hours.emergency -->\n## Emergency\n\nAlways open.\n"
    )
    sections = parse(text, "en")
    assert [s.section_id for s in sections] == ["hours.opening", "hours.emergency"]
    assert sections[0] == Section(
        "hours.opening", "en", "When we are open", ("Open from 7:00. Every day.", "Closed never.")
    )


def test_a_section_without_a_heading_or_body_is_refused() -> None:
    with pytest.raises(ValueError, match="needs a heading and a body"):
        parse("<!-- section: hours.opening -->\nNo heading here.\n", "en")
    with pytest.raises(ValueError, match="needs a heading and a body"):
        parse("<!-- section: hours.opening -->\n## A heading only\n", "en")


def test_a_long_section_is_split_at_paragraph_breaks() -> None:
    section = Section("a.long", "en", "Long", (words(200, "one"), words(200, "two"), words(100)))
    chunks = chunks_of(section)
    assert [c.chunk_no for c in chunks] == [0, 1]
    assert chunks[0].body == words(200, "one")
    assert chunks[1].body == f"{words(200, 'two')}\n\n{words(100)}"
    assert all(len(c.body.split()) <= MAX_WORDS for c in chunks)
    assert {c.title for c in chunks} == {"Long"}  # each chunk keeps the section's title


def test_a_single_paragraph_longer_than_the_limit_stays_whole() -> None:
    section = Section("a.huge", "en", "Huge", (words(400),))
    assert [len(c.body.split()) for c in chunks_of(section)] == [400]


def test_a_short_section_is_one_chunk() -> None:
    section = Section("a.short", "en", "Short", ("First.", "Second."))
    assert chunks_of(section) == [Chunk("a.short", 0, "en", "Short", "First.\n\nSecond.")]


def test_the_content_hash_and_version_change_with_the_text() -> None:
    first = Chunk("a.b", 0, "en", "Title", "Body.")
    changed = Chunk("a.b", 0, "en", "Title", "Body!")
    assert first.sha256 == Chunk("a.b", 0, "en", "Title", "Body.").sha256  # deterministic
    assert first.sha256 != changed.sha256
    assert handbook_version([first]) != handbook_version([changed])


def test_a_section_id_used_twice_in_one_language_is_refused(tmp_path: Path) -> None:
    for lang in ("en", "ar"):
        (tmp_path / lang).mkdir()
    section = "<!-- section: a.twice -->\n## Title\n\nBody.\n"
    (tmp_path / "en" / "one.md").write_text(section, encoding="utf-8")
    (tmp_path / "en" / "two.md").write_text(section, encoding="utf-8")
    with pytest.raises(ValueError, match="used twice"):
        load(tmp_path)


def test_the_shipped_handbook_has_the_same_sections_in_both_languages() -> None:
    """Each file has the same section ids, in the same order, in English and Arabic."""
    for english in sorted((DEFAULT_HANDBOOK / "en").glob("*.md")):
        arabic = DEFAULT_HANDBOOK / "ar" / english.name
        ids_en = [s.section_id for s in parse(english.read_text(encoding="utf-8"), "en")]
        ids_ar = [s.section_id for s in parse(arabic.read_text(encoding="utf-8"), "ar")]
        assert ids_en == ids_ar, english.name
    en_files = {p.name for p in (DEFAULT_HANDBOOK / "en").glob("*.md")}
    assert en_files == {p.name for p in (DEFAULT_HANDBOOK / "ar").glob("*.md")}


def test_the_shipped_handbook_matches_its_outline() -> None:
    """OUTLINE.md lists every file and its sections; the handbook has exactly those."""
    listed: dict[str, list[str]] = {}
    for line in OUTLINE.read_text(encoding="utf-8").splitlines():
        row = re.match(r"\| `([a-z_]+\.md)` \| (.+) \|$", line)
        if row:
            listed[row.group(1)] = re.findall(r"`([a-z_.]+)`", row.group(2))
    found = {
        path.name: [s.section_id for s in parse(path.read_text(encoding="utf-8"), "en")]
        for path in sorted((DEFAULT_HANDBOOK / "en").glob("*.md"))
    }
    assert found == listed
    chunks = load(DEFAULT_HANDBOOK)
    assert len({c.section_id for c in chunks}) == 25
    assert len(chunks) == 50  # every section is one chunk, in each language
