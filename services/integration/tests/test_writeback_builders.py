"""The write-back resources and Bundle (M1-09): golden files, determinism, codes and safety.

Golden files live in fixtures/writeback/golden/<record>/<local id>.json. After an intended change,
rewrite them with `CAREFLOW_UPDATE_GOLDEN=1 uv run pytest services/integration -k golden` and
review the diff.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from careflow_contracts.generated.structured_facts_schema import FactKey
from careflow_integration.adapters.loader import HospitalAdapter
from careflow_integration.fhir.client import FhirError, canonical_json
from careflow_integration.fhir.writeback.builders import build_resources
from careflow_integration.fhir.writeback.bundle import build_transaction, written_resources
from careflow_integration.fhir.writeback.record import News2Summary, TriageRecord

type Json = dict[str, Any]
type Load = Callable[[str], TriageRecord]

GOLDEN = Path(__file__).parent / "fixtures" / "writeback" / "golden"
RECORDS = ("chest_pain", "sepsis", "routine")
UPDATE = os.environ.get("CAREFLOW_UPDATE_GOLDEN") == "1"


def by_type(bundle: Json, resource_type: str) -> list[Json]:
    return [
        e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == resource_type
    ]


def one(bundle: Json, local_id: str) -> Json:
    [found] = [
        e["resource"] for e in bundle["entry"] if e["request"]["ifNoneExist"].endswith(local_id)
    ]
    return found


def references(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        if isinstance(ref := node.get("reference"), str):
            yield ref
        for value in node.values():
            yield from references(value)
    elif isinstance(node, list):
        for value in node:
            yield from references(value)


def tags(resource: Json) -> list[str]:
    return [t["code"] for t in resource["meta"]["tag"] if t["system"].endswith("provenance-source")]


@pytest.mark.parametrize("name", RECORDS)
def test_golden_every_builder(name: str, triage_record: Load, adapter: HospitalAdapter) -> None:
    built = build_resources(triage_record(name), adapter)
    folder = GOLDEN / name
    if UPDATE:
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("*.json"):
            old.unlink()
        for b in built:
            text = json.dumps(b.resource, indent=1, sort_keys=True, ensure_ascii=False)
            (folder / f"{b.local_id}.json").write_text(text + "\n", encoding="utf-8")
    assert sorted(p.stem for p in folder.glob("*.json")) == sorted(b.local_id for b in built)
    for b in built:
        golden = json.loads((folder / f"{b.local_id}.json").read_text(encoding="utf-8"))
        assert b.resource == golden, b.local_id


@pytest.mark.parametrize("name", RECORDS)
def test_the_same_record_builds_a_byte_identical_bundle_twice(
    name: str, triage_record: Load, adapter: HospitalAdapter
) -> None:
    first = canonical_json(build_transaction(triage_record(name), adapter))
    again = canonical_json(build_transaction(triage_record(name), adapter))
    assert first == again


@pytest.mark.parametrize("name", RECORDS)
def test_every_entry_is_an_idempotent_conditional_create(
    name: str, triage_record: Load, adapter: HospitalAdapter
) -> None:
    bundle = build_transaction(triage_record(name), adapter)
    assert bundle["type"] == "transaction"
    full_urls = [e["fullUrl"] for e in bundle["entry"]]
    assert len(set(full_urls)) == len(full_urls)
    assert all(u.startswith("urn:uuid:") for u in full_urls)
    for entry in bundle["entry"]:
        resource, request = entry["resource"], entry["request"]
        assert request["method"] == "POST" and request["url"] == resource["resourceType"]
        if resource["resourceType"] == "Provenance":
            assert request["ifNoneExist"] == f"_tag=urn:careflow:writeback|ses_{name}"
            continue
        identifiers = resource["identifier"]
        [identifier] = identifiers if isinstance(identifiers, list) else [identifiers]
        assert request["ifNoneExist"] == f"identifier={identifier['system']}|{identifier['value']}"
    # Every reference is the EHR patient or another entry of this Bundle.
    for ref in references(bundle):
        assert ref in full_urls or ref == f"Patient/persona-{name.replace('_', '-')}", ref


@pytest.mark.parametrize("name", RECORDS)
def test_every_resource_carries_its_provenance_subject_and_encounter(
    name: str, triage_record: Load, adapter: HospitalAdapter
) -> None:
    bundle = build_transaction(triage_record(name), adapter)
    encounter_url = bundle["entry"][0]["fullUrl"]
    for entry in bundle["entry"]:
        resource = entry["resource"]
        assert tags(resource), resource["resourceType"]
        if resource["resourceType"] in {"Encounter", "Provenance"}:
            continue
        assert resource["encounter"] == {"reference": encounter_url}
        subject = resource.get("subject", resource.get("for"))
        assert subject == {"reference": f"Patient/persona-{name.replace('_', '-')}"}


def test_the_encounter_is_the_nurse_confirmed_triage(
    triage_record: Load, adapter: HospitalAdapter
) -> None:
    bundle = build_transaction(triage_record("chest_pain"), adapter)
    encounter = one(bundle, "urn:careflow:session|ses_chest_pain")
    assert encounter["status"] == "triaged"
    assert encounter["class"]["code"] == "EMER"  # demo_hapi: an emergency department
    assert [c["code"] for c in encounter["priority"]["coding"]] == ["EM", "KTAS-2"]
    assert encounter["priority"]["text"] == "KTAS level 2, confirmed by the triage nurse"
    assert encounter["period"] == {"start": "2026-09-26T09:00:00Z"}
    assert tags(encounter) == ["NURSE_CONFIRMED", "MODEL_INFERRED"]  # the complaint category
    amb = build_transaction(
        triage_record("chest_pain"), adapter.model_copy(update={"encounter_class": "AMB"})
    )
    assert amb["entry"][0]["resource"]["class"]["code"] == "AMB"


def test_only_the_nurses_level_is_written(triage_record: Load, adapter: HospitalAdapter) -> None:
    """Safety rule 3: the routine record's nurse overrode the suggestion to level 4."""
    record = triage_record("routine")
    bundle = build_transaction(record, adapter)
    level = one(bundle, ":obs-triage-level")
    assert level["valueInteger"] == record.decision.final_level == 4
    assert level["performer"] == [
        {
            "identifier": {"system": "urn:careflow:staff", "value": "nurse_demo_01"},
            "display": "Triage nurse",
        }
    ]
    encounter = bundle["entry"][0]["resource"]
    assert encounter["priority"]["text"] == "KTAS level 4, set by the triage nurse (override)"
    assert [c["code"] for c in encounter["priority"]["coding"]] == ["R", "KTAS-4"]


def test_vitals_use_the_mapping_codes_and_the_right_attribution(
    triage_record: Load, adapter: HospitalAdapter
) -> None:
    bundle = build_transaction(triage_record("chest_pain"), adapter)
    spo2 = one(bundle, ":obs-spo2")
    assert [c["code"] for c in spo2["code"]["coding"]] == ["2708-6", "59408-5"]
    assert spo2["valueQuantity"] == {
        "value": 97,
        "unit": "%",
        "system": "http://unitsofmeasure.org",
        "code": "%",
    }
    assert spo2["device"]["identifier"] == {
        "system": "urn:careflow:device",
        "value": "sim_vitals_01",
    }
    assert tags(spo2) == ["DEVICE_MEASURED"]
    bp = one(bundle, ":obs-blood-pressure")
    assert bp["code"]["coding"][0]["code"] == "85354-9"
    assert [
        (c["code"]["coding"][0]["code"], c["valueQuantity"]["value"]) for c in bp["component"]
    ] == [
        ("8480-6", 158),
        ("8462-4", 94),
    ]
    rr = one(bundle, ":obs-respiratory-rate")  # entered by the nurse
    assert "device" not in rr and rr["performer"][0]["identifier"]["value"] == "nurse_demo_01"
    assert tags(rr) == ["NURSE_CONFIRMED"]
    acvpu = one(bundle, ":obs-acvpu")
    assert acvpu["valueCodeableConcept"]["coding"][0]["code"] == "248234008"  # Mentally alert
    assert one(bundle, ":obs-supplemental-o2")["code"]["coding"][0]["code"] == "722742002"  # air
    pain = one(bundle, ":obs-pain-score")
    assert pain["performer"] == [{"reference": "Patient/persona-chest-pain"}]
    assert tags(pain) == ["PATIENT_STATED"]


def test_a_partial_news2_is_preliminary_with_the_missing_inputs(
    triage_record: Load, adapter: HospitalAdapter
) -> None:
    full = one(build_transaction(triage_record("chest_pain"), adapter), ":obs-news2")
    assert full["status"] == "final" and "note" not in full and full["valueQuantity"]["value"] == 1
    assert len(full["derivedFrom"]) == 7  # HR, RR, SpO2, temp, BP, ACVPU, air or oxygen
    partial = one(build_transaction(triage_record("sepsis"), adapter), ":obs-news2")
    assert partial["status"] == "preliminary" and partial["valueQuantity"]["value"] == 6
    assert partial["note"] == [
        {
            "text": "Partial NEWS2 from the inputs measured so far. Missing: respiratory rate, "
            "systolic blood pressure, ACVPU, air or oxygen."
        }
    ]
    assert partial["category"][0]["coding"][0]["code"] == "survey"


def test_a_news2_with_nothing_measured_is_left_out_and_the_rest_is_written(
    triage_record: Load, adapter: HospitalAdapter
) -> None:
    """A patient who hands over to the phone before the kiosk's vitals has no vital sign, but the
    rules keep a partial NEWS2 with every input missing. That total derives from nothing, so it
    has no provenance: writing it made the whole Bundle fail (safety rule 4) and the nurse's level
    never reached the EHR (found by the M2-07 journeys, fixed in M2-01)."""
    routine = triage_record("routine")
    unmeasured = [key for key in FactKey if key.value.startswith("vitals.")]
    record = routine.model_copy(
        update={
            "facts": routine.facts.model_copy(update={"vitals": None}),
            "news2": News2Summary(
                total=0, partial=True, missing=unmeasured, scored_at=routine.arrived_at
            ),
        }
    )
    bundle = build_transaction(record, adapter)
    local_ids = [e["request"]["ifNoneExist"].rsplit(":", 1)[-1] for e in bundle["entry"]]
    assert "obs-news2" not in local_ids
    assert {i for i in local_ids if i.startswith("obs-")} <= {"obs-triage-level", "obs-pain-score"}
    assert one(bundle, ":obs-triage-level")["valueInteger"] == int(record.decision.final_level)
    assert by_type(bundle, "Encounter")


def test_the_questionnaire_response_holds_only_what_the_patient_said(
    triage_record: Load, adapter: HospitalAdapter
) -> None:
    bundle = build_transaction(triage_record("sepsis"), adapter)
    [qr] = by_type(bundle, "QuestionnaireResponse")
    assert (
        qr["questionnaire"] == "https://careflow.example/fhir/Questionnaire/careflow-intake|0.1.0"
    )
    assert qr["status"] == "completed" and qr["source"] == {"reference": "Patient/persona-sepsis"}
    links = [item["linkId"] for item in qr["item"]]
    assert "patient" not in links  # age and sex come from the EHR, not from an answer
    [symptom] = [item for item in qr["item"] if item["linkId"] == "symptoms"]
    assert symptom["item"] == [
        {"linkId": "symptoms.name", "answer": [{"valueString": "productive_cough"}]},
        {"linkId": "symptoms.onset", "answer": [{"valueString": "P3D"}]},
        {
            "linkId": "symptoms.severity",
            "answer": [
                {
                    "valueCoding": {
                        "system": "https://careflow.example/fhir/CodeSystem/symptom-severity",
                        "code": "moderate",
                        "display": "Moderate",
                    }
                }
            ],
        },
    ]
    [infection] = [item for item in qr["item"] if item["linkId"] == "infection"]
    assert [i["linkId"] for i in infection["item"]] == [
        "infection.fever_reported",
        "infection.rigors",
    ]


def test_the_ecg_task_is_due_at_the_alert_deadline(
    triage_record: Load, adapter: HospitalAdapter
) -> None:
    record = triage_record("chest_pain")
    bundle = build_transaction(record, adapter)
    [task] = by_type(bundle, "Task")
    [alert] = record.alerts
    assert alert.deadline_at is not None
    assert task["restriction"]["period"]["end"] == "2026-09-26T09:10:00Z"
    assert task["restriction"]["period"]["end"] == alert.deadline_at.isoformat().replace(
        "+00:00", "Z"
    )
    assert (task["status"], task["intent"], task["priority"]) == ("requested", "order", "stat")
    assert (
        task["code"]["text"] == "12-lead ECG"
        and task["code"]["coding"][0]["code"] == "ecg_within_10_min"
    )
    [flag] = [e["fullUrl"] for e in bundle["entry"] if e["resource"]["resourceType"] == "Flag"]
    assert task["focus"] == {"reference": flag}
    done = record.model_copy(update={"completed_actions": ["ecg_within_10_min"]})
    [written_done] = by_type(build_transaction(done, adapter), "Task")
    assert written_done["status"] == "completed"
    assert not by_type(build_transaction(triage_record("sepsis"), adapter), "Task")  # no deadline


def test_one_flag_per_alert_with_the_rule_code_and_the_evidence_provenance(
    triage_record: Load, adapter: HospitalAdapter
) -> None:
    [flag] = by_type(build_transaction(triage_record("chest_pain"), adapter), "Flag")
    assert flag["code"]["coding"][0]["code"] == "CHEST_PAIN_ECG_10MIN"
    assert flag["code"]["text"] == "Chest pain: ECG within 10 minutes of arrival."
    assert flag["category"][0]["coding"][0]["code"] == "clinical"
    assert tags(flag) == ["PATIENT_STATED"]  # the patient said they have chest pain
    assert not by_type(build_transaction(triage_record("routine"), adapter), "Flag")


def test_the_handoff_uses_fixed_templates_and_never_the_patients_words(
    triage_record: Load, adapter: HospitalAdapter
) -> None:
    record = triage_record("chest_pain")
    [comm] = by_type(build_transaction(record, adapter), "Communication")
    text = comm["payload"][0]["contentString"]
    assert text.splitlines() == [
        "Triage: KTAS level 2, confirmed by the triage nurse.",
        "Alerts: Chest pain: ECG within 10 minutes of arrival.",
        "Vitals: HR 96/min, RR 18/min, SpO2 97%, temp 36.8 °C, BP 158/94 mmHg, ACVPU A, on air.",
        "NEWS2: 1.",
        "Positive screens: chest pain (present, radiates, sweating).",
        "Pain score: 8/10.",
        "Complaint category (model-inferred): Chest pain.",
    ]
    assert record.facts.chief_complaint is not None
    assert record.facts.chief_complaint.value.lower() not in text.lower()
    assert (
        comm["status"] == "completed"
        and comm["recipient"][0]["identifier"]["value"] == "nurse_demo_01"
    )
    assert set(tags(comm)) == {
        "PATIENT_STATED",
        "DEVICE_MEASURED",
        "NURSE_CONFIRMED",
        "MODEL_INFERRED",
    }


def test_the_provenance_names_every_source_and_targets_every_resource(
    triage_record: Load, adapter: HospitalAdapter
) -> None:
    bundle = build_transaction(triage_record("chest_pain"), adapter)
    [provenance] = by_type(bundle, "Provenance")
    others = [e["fullUrl"] for e in bundle["entry"][:-1]]
    assert [t["reference"] for t in provenance["target"]] == others
    assert [a["type"]["coding"][0]["code"] for a in provenance["agent"]] == [
        "performer",
        "assembler",
        "author",
        "informant",
    ]
    assert provenance["recorded"] == "2026-09-26T09:06:00Z"  # the decision, not the clock
    assert {"system": "urn:careflow:writeback", "code": "ses_chest_pain"} in provenance["meta"][
        "tag"
    ]


def test_no_patient_or_missing_evidence_is_refused(
    triage_record: Load, adapter: HospitalAdapter
) -> None:
    record = triage_record("chest_pain")
    with pytest.raises(ValueError, match="no EHR patient"):
        build_transaction(record.model_copy(update={"patient_ref": None}), adapter)
    [alert] = record.alerts
    orphan = alert.model_copy(update={"evidence_fact_ids": ["f_0000000000000000000000ZZZZ"]})
    with pytest.raises(ValueError, match="evidence"):
        build_transaction(record.model_copy(update={"alerts": [orphan]}), adapter)


def transaction_response(bundle: Json, status: str = "201 Created", absolute: bool = False) -> Json:
    base = "http://fhir.test/fhir/" if absolute else ""
    return {
        "resourceType": "Bundle",
        "type": "transaction-response",
        "entry": [
            {
                "response": {
                    "status": status,
                    "location": f"{base}{e['resource']['resourceType']}/{100 + i}/_history/1",
                }
            }
            for i, e in enumerate(bundle["entry"])
        ],
    }


def test_the_transaction_response_is_read_entry_by_entry(
    triage_record: Load, adapter: HospitalAdapter
) -> None:
    bundle = build_transaction(triage_record("chest_pain"), adapter)
    written = written_resources(bundle, transaction_response(bundle))
    assert [w.local_id for w in written][:3] == [
        "encounter",
        "obs-heart-rate",
        "obs-respiratory-rate",
    ]
    assert written[-1].local_id == "provenance" and written[-3].local_id == "task-ecg_within_10_min"
    assert all(w.created and w.version_id == "1" for w in written)
    again = written_resources(bundle, transaction_response(bundle, "200 OK", absolute=True))
    assert [w.resource_id for w in again] == [w.resource_id for w in written]
    assert not any(w.created for w in again)
    short = transaction_response(bundle)
    short["entry"].pop()
    with pytest.raises(FhirError, match="unexpected transaction-response"):
        written_resources(bundle, short)
