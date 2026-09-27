"""The handbook is fictional (M2-08 review item 2): no real hospital or insurer name appears in it,
in English or Arabic, and its clinic and insurers are the fictional ones."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from careflow_rag.ingest import DEFAULT_HANDBOOK

DENYLIST: Final = DEFAULT_HANDBOOK / "denylist.txt"


def denied() -> list[str]:
    lines = DENYLIST.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def found_in(text: str, names: list[str]) -> list[str]:
    return [
        name for name in names if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE)
    ]


def handbook_files() -> list[Path]:
    return sorted(p for lang in ("en", "ar") for p in (DEFAULT_HANDBOOK / lang).glob("*.md"))


def test_no_real_hospital_or_insurer_name_is_in_the_handbook() -> None:
    names = denied()
    assert len(names) >= 50
    hits = {p.name: found_in(p.read_text(encoding="utf-8"), names) for p in handbook_files()}
    assert {name: found for name, found in hits.items() if found} == {}


def test_the_denylist_would_catch_a_real_name() -> None:
    names = denied()
    assert found_in("Show your Daman card at reception.", names) == ["Daman"]
    assert found_in("Near Cleveland Clinic Abu Dhabi.", names) == [
        "Cleveland Clinic",
        "Cleveland Clinic Abu Dhabi",
    ]
    assert found_in("تقبل العيادة تأمين بوبا.", names) == ["بوبا"]
    assert found_in("Damanhour is a city.", names) == []  # whole words only


def test_the_handbook_names_only_the_fictional_clinic_and_insurers() -> None:
    text = "\n".join(p.read_text(encoding="utf-8") for p in handbook_files())
    assert "CareFlow Demo Clinic" in text
    assert set(re.findall(r"Insurer [A-Z]\b", text)) == {"Insurer A", "Insurer B", "Insurer C"}
