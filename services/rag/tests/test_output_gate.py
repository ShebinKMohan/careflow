"""The patient-output safety gate (M2-09, SHE-306): what may reach a patient after the citations
were checked, in English and Arabic."""

from __future__ import annotations

from typing import Final

import pytest

from careflow_rag.handbook import load
from careflow_rag.ingest import DEFAULT_HANDBOOK
from careflow_rag.output_gate import check_answer

CHUNKS: Final = load(DEFAULT_HANDBOOK)
PHARMACY_EN: Final = next(c for c in CHUNKS if c.section_id == "pharmacy.hours" and c.lang == "en")
PHARMACY_AR: Final = next(c for c in CHUNKS if c.section_id == "pharmacy.hours" and c.lang == "ar")
DOCUMENTS_EN: Final = next(
    c for c in CHUNKS if c.section_id == "insurance.documents" and c.lang == "en"
)


def source(chunk: object) -> str:
    return f"{chunk.title}\n{chunk.body}"  # type: ignore[attr-defined]


@pytest.mark.parametrize("chunk", CHUNKS, ids=lambda c: f"{c.section_id}-{c.lang}")
def test_every_handbook_section_as_an_answer_passes_the_gate(chunk: object) -> None:
    """No false alarm on the reviewed handbook itself: each section, quoted as the answer that
    cites it, may reach the patient ("Bring a list of the medicines you take" included)."""
    verdict = check_answer(chunk.body, source(chunk))  # type: ignore[attr-defined]
    assert verdict.allowed, verdict.reasons


UNSAFE_EN: Final = [
    ("The pharmacy opens at 8:00. Take 2 tablets of paracetamol for your headache.", "dose"),
    ("The pharmacy opens at 8:00. A dose of 500 mg is usual.", "dose"),
    ("It sounds like you have an infection. The pharmacy opens at 8:00.", "diagnosis"),
    ("You probably have the flu; the pharmacy opens at 8:00.", "diagnosis"),
    ("Don't worry, the pharmacy opens at 8:00.", "advice"),
    ("It is nothing serious. The pharmacy opens at 8:00.", "advice"),
    ("You can go home and come back when the pharmacy opens at 8:00.", "advice"),
    ("It can wait until the pharmacy opens at 8:00.", "advice"),
    ("Keep taking your antibiotics; the pharmacy opens at 8:00.", "advice"),
    ("The pharmacy opens at 8:00 and ibuprofen helps with the pain.", "medical term"),
]
UNSAFE_AR: Final = [
    ("تفتح الصيدلية الساعة 8:00. خذ حبتين من الباراسيتامول، 500 ملغ.", "dose"),
    ("تفتح الصيدلية الساعة 8:00. على الأرجح لديك التهاب.", "diagnosis"),
    ("لا تقلق، تفتح الصيدلية الساعة 8:00.", "advice"),
    ("الأمر ليس خطيراً. تفتح الصيدلية الساعة 8:00.", "advice"),
    ("يمكنك العودة إلى البيت. تفتح الصيدلية الساعة 8:00.", "advice"),
    ("تفتح الصيدلية الساعة 8:00 والمضادات الحيوية تساعد على الشفاء.", "medical term"),
]


@pytest.mark.parametrize(("answer", "reason"), UNSAFE_EN)
def test_an_unsafe_english_answer_citing_a_real_section_is_blocked(
    answer: str, reason: str
) -> None:
    verdict = check_answer(answer, source(PHARMACY_EN))
    assert not verdict.allowed
    assert any(r.startswith(reason) for r in verdict.reasons), verdict.reasons


@pytest.mark.parametrize(("answer", "reason"), UNSAFE_AR)
def test_an_unsafe_arabic_answer_citing_a_real_section_is_blocked(answer: str, reason: str) -> None:
    verdict = check_answer(answer, source(PHARMACY_AR))
    assert not verdict.allowed
    assert any(r.startswith(reason) for r in verdict.reasons), verdict.reasons


def test_a_medical_word_is_allowed_only_when_the_cited_source_has_it() -> None:
    answer = "Bring your identity card and a list of the medicines you take."
    assert check_answer(answer, source(DOCUMENTS_EN)).allowed  # the source says "medicines"
    assert not check_answer(answer, source(PHARMACY_EN)).allowed  # this one does not
    # "pills" is its own entry: "medicines" in the source does not ground it.
    assert not check_answer("Bring a list of the pills you take.", source(DOCUMENTS_EN)).allowed


def test_practical_answers_with_numbers_and_queue_data_pass() -> None:
    assert check_answer(
        "You are number 3 in the queue; about 15 minutes.", '{"position": 3}'
    ).allowed
    assert check_answer(
        "You can take the prescription to any other pharmacy.", "prescription"
    ).allowed
    assert check_answer("غرفتك في الطابق الأول، الغرفة 3.", "الطابق الأول").allowed
