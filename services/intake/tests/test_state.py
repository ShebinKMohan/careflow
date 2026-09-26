"""FactEntry, the reducers and the StructuredFacts built from the state."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from careflow_contracts.generated.alert_schema import Alert
from careflow_contracts.generated.fact_schema import CodeFact, TextFact, TriStateFact
from careflow_contracts.generated.structured_facts_schema import (
    EhrContext,
    FactKey,
    HeartRateFact,
    Language,
    Symptom,
)
from careflow_intake.facts import FactEntry, fact_class, merge_facts_by_id, to_structured_facts
from careflow_intake.state import merge_alerts_by_rule_id, merge_flags
from careflow_intake.stubs import stable_fact_id

T0 = "2026-09-27T09:00:00Z"


def stated(key: str, value: Any, *, n: int = 1, at: str = T0) -> dict[str, Any]:
    return {
        "key": key,
        "fact": {
            "fact_id": stable_fact_id("s", key, str(n)),
            "provenance": "PATIENT_STATED",
            "source": {
                "kind": "transcript_span",
                "turn_id": "trn_002",
                "start": 0,
                "end": 3,
                "quote": "yes",
            },
            "recorded_at": at,
            "value": value,
        },
    }


def test_fact_class_follows_the_contract() -> None:
    assert fact_class(FactKey.vitals_heart_rate) is HeartRateFact
    assert fact_class(FactKey.chest_pain_present) is TriStateFact
    assert fact_class(FactKey.chief_complaint) is TextFact
    assert fact_class(FactKey.symptoms) is Symptom
    assert fact_class(FactKey.ehr_context_allergies) is CodeFact
    for key in FactKey:  # every contract key resolves
        assert fact_class(key)


def test_a_fact_entry_is_typed_by_its_key() -> None:
    entry = FactEntry.model_validate(stated("chest_pain.present", "yes"))
    assert isinstance(entry.fact, TriStateFact) and entry.fact_id.startswith("f_")
    with pytest.raises(ValidationError):  # a tri-state key cannot hold a number
        FactEntry.model_validate(stated("chest_pain.present", 7))
    with pytest.raises(ValidationError):
        FactEntry.model_validate({**stated("chest_pain.present", "yes"), "key": "not.a.key"})


def test_facts_merge_by_id() -> None:
    first = FactEntry.model_validate(stated("chest_pain.present", "no"))
    same_id = FactEntry.model_validate(stated("chest_pain.present", "yes"))
    other = FactEntry.model_validate(stated("infection.fever_reported", "no", n=2))
    merged = merge_facts_by_id([first], [other, same_id.model_dump(mode="json")])
    assert [entry.fact_id for entry in merged] == [first.fact_id, other.fact_id]
    assert merged[0].fact.model_dump()["value"] == "yes"  # replaced in place


def alert(rule_id: str, fact_id: str) -> Alert:
    return Alert.model_validate(
        {
            "alert_id": f"alr_{rule_id.lower()}",
            "session_id": "s",
            "rule_id": rule_id,
            "ruleset_version": "1.0.0",
            "severity": "critical",
            "evidence_fact_ids": [fact_id],
            "required_action": "review_now",
            "partial": False,
            "raised_at": T0,
        }
    )


def test_alerts_merge_by_rule_id_and_are_never_removed() -> None:
    fid = stable_fact_id("x")
    fast, chest = alert("FAST_STROKE", fid), alert("CHEST_PAIN_ECG_10MIN", fid)
    merged = merge_alerts_by_rule_id([fast], [chest, alert("FAST_STROKE", fid)])
    assert [a.rule_id.value for a in merged] == ["FAST_STROKE", "CHEST_PAIN_ECG_10MIN"]
    assert merge_alerts_by_rule_id(merged, []) == merged


def test_flags_only_turn_on() -> None:
    flags = merge_flags({"ehr_unavailable": True}, {"extraction_incomplete": True})
    assert flags == {"ehr_unavailable": True, "extraction_incomplete": True}
    assert merge_flags(flags, {"ehr_unavailable": False}).get("ehr_unavailable") is True


def test_structured_facts_from_entries() -> None:
    ehr = EhrContext.model_validate(
        {
            "allergies": [
                {
                    "fact_id": stable_fact_id("allergy"),
                    "provenance": "EHR",
                    "source": {"kind": "fhir", "resource_type": "AllergyIntolerance", "id": "a1"},
                    "recorded_at": T0,
                    "value": {
                        "system": "http://snomed.info/sct",
                        "code": "91936005",
                        "display": "Penicillin allergy",
                    },
                }
            ]
        }
    )
    entries = [
        FactEntry.model_validate(stated("chest_pain.present", "yes")),
        FactEntry.model_validate(stated("chief_complaint", "Tight chest", n=2)),
        FactEntry.model_validate(
            {"key": "symptoms", "fact": {"name": stated("chief_complaint", "nausea", n=3)["fact"]}}
        ),
        FactEntry.model_validate(
            stated("chest_pain.present", "no", n=4, at="2026-09-27T09:01:00Z")
        ),
    ]
    facts = to_structured_facts(
        session_id="ses_1",
        clinic_id="clinic_a",
        language=Language.en,
        entries=entries,
        ehr_context=ehr,
    )
    assert facts.chest_pain is not None and facts.chest_pain.present is not None
    assert facts.chest_pain.present.value.value == "no"  # the later statement is current
    assert facts.chief_complaint is not None and facts.chief_complaint.value == "Tight chest"
    assert facts.symptoms is not None and [s.name.value for s in facts.symptoms] == ["nausea"]
    assert facts.ehr_context is not None and facts.ehr_context.allergies is not None
    assert facts.ehr_context.allergies[0].value.display == "Penicillin allergy"
