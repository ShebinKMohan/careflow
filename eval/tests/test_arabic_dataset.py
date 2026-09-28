"""The approved Arabic corpus (M2-03, SHE-290): at least 90 independently approved scenarios from
both dialects, every rule id expected at least 3 times, and each approved scenario's answers
passed the M2-01 back-translation check, as its review log entry records."""

from __future__ import annotations

import json
from collections import Counter
from functools import cache
from typing import Any

from careflow_contracts.generated.alert_schema import RuleId
from careflow_contracts.generated.structured_facts_schema import Language
from careflow_eval.datasets import DATASETS_DIR, load_dataset
from careflow_eval.identity import model_family
from careflow_eval.models import Dataset

ARABIC = DATASETS_DIR / "intake_ar"
LOG = DATASETS_DIR / "review-log.jsonl"


@cache
def approved() -> Dataset:
    return load_dataset(ARABIC)  # the default loader: reviewed, hash-bound, distinct family


@cache
def log() -> list[dict[str, Any]]:
    return [json.loads(line) for line in LOG.read_text(encoding="utf-8").splitlines() if line]


def test_at_least_90_approved_arabic_scenarios_from_both_dialects() -> None:
    cases = approved().scenarios
    assert len(cases) >= 90
    assert all(case.language is Language.ar and case.synthetic for case in cases)
    dialects = Counter(case.dialect for case in cases)
    assert set(dialects) == {"gulf", "levantine"}
    assert min(dialects.values()) >= 40, dialects
    for case in cases:
        assert case.reviewed_by is not None and case.review_method == "automated"
        assert model_family(case.generated_by.model) != model_family(case.reviewed_by)


def test_every_rule_id_is_expected_at_least_three_times() -> None:
    covered = Counter(rule.value for case in approved().scenarios for rule in case.expected.alerts)
    assert all(covered[rule.value] >= 3 for rule in RuleId), covered


def test_every_approved_answer_passed_the_back_translation_check() -> None:
    accepted = {
        (entry["id"], entry["content_sha256"]): entry
        for entry in log()
        if entry["decision"] == "accepted"
    }
    for case in approved().scenarios:
        entry = accepted[(case.id, case.reviewed_content_sha256)]
        assert entry["dialect"] == case.dialect
        rows = entry["backtranslation"]
        assert [row["arabic"] for row in rows] == [turn.patient_text for turn in case.turns]
        assert [row["gloss_en"] for row in rows] == [turn.gloss_en for turn in case.turns]
        for row in rows:
            assert row["same_meaning"] and row["chrf"] >= 40 and not row["flagged"], (case.id, row)


def test_failed_arabic_reviews_are_kept_with_their_reasons() -> None:
    arabic = {case.id for case in approved().scenarios} | {
        path.stem for path in (ARABIC / "_inbox").glob("*.yaml")
    }
    for entry in log():
        if entry["id"] in arabic and entry["decision"] == "rejected":
            assert entry["reasons"] and entry["stage"] in {
                "deterministic-validation",
                "back-translation",
                "independent-model",
            }
