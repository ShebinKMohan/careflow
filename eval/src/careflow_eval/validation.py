"""Offline scenario checks: contract types, identifier denylist, provenance and rules."""

from __future__ import annotations

import json
import re
from functools import cache
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from careflow_eval.datasets import ARRIVED_AT, NOW, SCHEMA_PATH, context_entries, expected_entries
from careflow_eval.models import Scenario
from careflow_intake.conversation.checklist import templates_for
from careflow_intake.extraction.prompt import extractable_keys
from careflow_intake.facts import to_structured_facts
from careflow_rules import evaluate

# A denylist is a screening gate, not a claim that regex can establish de-identification.
# The different-model review also checks that scenarios are wholly invented.
PHI_DENYLIST = {
    "email address": r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b",
    "UK postcode": r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b",
    "phone or NHS number": r"(?<!\d)(?:\+?\d[ .()-]*){10,15}(?!\d)",
    "birth date or named patient": r"\b(?:date of birth|dob|my name is|patient name)\b",
    "street address": r"\b\d{1,5}\s+[A-Za-z ]+\s+(?:street|road|avenue|lane|drive)\b",
    "Arabic name or birth date": r"(?:اسمي|إسمي|تاريخ ميلادي|رقم هويتي|رقم الهوية)",
}


@cache
def _schema_validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_scenario(scenario: Scenario | dict[str, Any] | Any) -> list[str]:
    """Return every schema/PHI error, then structural and exact alert-consistency errors.

    ``expected.level`` is an illustrative synthetic label, not a clinical validity claim;
    its range and rationale are required, while rule alerts are checked against real code.
    Review metadata is checked separately by the default dataset loader so drafts can be
    validated honestly without inventing review evidence.
    """
    body = scenario.model_dump(mode="json") if isinstance(scenario, Scenario) else scenario
    errors = [
        f"schema {'.'.join(map(str, error.absolute_path)) or '<root>'}: {error.message}"
        for error in sorted(
            _schema_validator().iter_errors(body), key=lambda e: str(e.absolute_path)
        )
    ]
    if errors:
        return errors
    try:
        case = Scenario.model_validate(body)
    except ValidationError as exc:
        return [f"contract: {error['loc']}: {error['msg']}" for error in exc.errors()]
    texts = [case.description, case.expected.level_rationale, *(t.patient_text for t in case.turns)]
    for label, pattern in PHI_DENYLIST.items():
        if any(re.search(pattern, text, re.IGNORECASE) for text in texts):
            errors.append(f"PHI denylist: {label}")
    bank = templates_for(case.language)
    approved = bank.keys() | {type(case.turns[0].question_key)(key) for key in bank.child_questions}
    for turn in case.turns:
        if turn.question_key not in approved:
            errors.append(f"no approved question template for {turn.question_key.value}")
    for key in case.expected.facts:
        if key not in extractable_keys():
            errors.append(f"gold key is outside extractor scope: {key.value}")
        if key.value.startswith("patient."):
            errors.append(f"gold key duplicates known patient context: {key.value}")
    if len(set(case.expected.alerts)) != len(case.expected.alerts):
        errors.append("expected alerts contain duplicates")
    for turn in case.turns:  # Arabic (M2-03): an answer states only gold keys
        for key in turn.states:
            if key not in case.expected.facts:
                errors.append(f"an answer states {key.value}, which has no gold fact")
    if errors:
        return errors
    try:
        entries = context_entries(case) + expected_entries(case)
        facts = to_structured_facts(
            session_id=f"eval-{case.id}",
            clinic_id="synthetic-eval",
            language=case.language,
            entries=entries,
        )
        result = evaluate(facts, NOW, arrived_at=ARRIVED_AT)
        actual = sorted({alert.rule_id.value for alert in result.alerts})
        expected = sorted(alert.value for alert in case.expected.alerts)
        if actual != expected:
            errors.append(f"rule alert mismatch: expected {expected}, computed {actual}")
    except (ValueError, KeyError, TypeError) as exc:
        errors.append(f"facts: {exc}")
    return errors
