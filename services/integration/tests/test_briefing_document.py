"""The doctor briefing as a FHIR document Bundle (M1-16), against recorded HAPI answers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fhir.resources.R4B.bundle import Bundle

from careflow_contracts.api_models import Briefing, BriefingSectionId
from careflow_integration.fhir.client import FhirClient
from careflow_integration.fhir.writeback.composition import (
    DOCUMENT_SYSTEM,
    build_document,
    document_match,
    needed_refs,
    references_in,
    write_document,
)
from careflow_integration.fhir.writeback.ports import WritebackStatus, WrittenResource

FIXTURES = Path(__file__).parent / "fixtures" / "briefing"
BASE = "http://fhir.test/fhir"
PROVENANCE_TAGS = "https://careflow.example/fhir/CodeSystem/provenance-source"

WRITTEN = [
    WrittenResource("encounter", "Encounter", "enc-1", "1"),
    WrittenResource("obs-heart-rate", "Observation", "obs-hr", "1"),
    WrittenResource("obs-spo2", "Observation", "obs-spo2", "1"),
    WrittenResource("obs-temperature", "Observation", "obs-temp", "1"),
    WrittenResource("obs-respiratory-rate", "Observation", "obs-rr", "1"),
    WrittenResource("obs-blood-pressure", "Observation", "obs-bp", "1"),
    WrittenResource("obs-acvpu", "Observation", "obs-acvpu", "1"),
    WrittenResource("obs-supplemental-o2", "Observation", "obs-o2", "1"),
    WrittenResource("obs-pain-score", "Observation", "obs-pain", "1"),
    WrittenResource("obs-news2", "Observation", "obs-news2", "1"),
    WrittenResource("obs-triage-level", "Observation", "obs-level", "1"),
    WrittenResource("qr-patient", "QuestionnaireResponse", "qr-1", "1"),
    WrittenResource("flag-CHEST_PAIN_ECG_10MIN", "Flag", "flag-1", "1"),
    WrittenResource("task-ecg_within_10_min", "Task", "task-1", "2"),
    WrittenResource("comm-handoff", "Communication", "comm-1", "1"),
    WrittenResource("provenance", "Provenance", "prov-1", "1"),
]


def load(persona: str) -> tuple[Briefing, str]:
    body = json.loads((FIXTURES / f"{persona}.json").read_text(encoding="utf-8"))
    return Briefing.model_validate(body["briefing"]), body["patient_ref"]


def server_resource(ref: str) -> dict[str, Any]:
    """A small valid resource of the ref's type, as the server would hold it."""
    kind, rid = ref.split("/")
    meta = {"versionId": "1", "lastUpdated": "2026-09-27T09:09:00Z"}
    subject = {"reference": "Patient/persona-chest-pain"}
    encounter = {"reference": "Encounter/enc-1"}
    bodies: dict[str, dict[str, Any]] = {
        "Patient": {"gender": "male", "birthDate": "1968-03-14"},
        "Encounter": {
            "status": "triaged",
            "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "EMER"},
            "subject": subject,
        },
        "Observation": {
            "status": "final",
            "code": {"text": "observation"},
            "subject": subject,
            "encounter": encounter,
        },
        "QuestionnaireResponse": {
            "status": "completed",
            "subject": subject,
            "encounter": encounter,
        },
        "Flag": {
            "status": "active",
            "code": {"text": "flag"},
            "subject": subject,
            "encounter": encounter,
        },
        "Task": {
            "status": "completed",
            "intent": "order",
            "focus": {"reference": "Flag/flag-1"},
            "for": subject,
            "encounter": encounter,
        },
        "Condition": {"code": {"text": "Hypertensive disorder"}, "subject": subject},
        "MedicationStatement": {
            "status": "active",
            "medicationCodeableConcept": {"text": "amlodipine"},
            "subject": subject,
        },
    }
    return {"resourceType": kind, "id": rid, "meta": meta, **bodies[kind]}


def resources_for(briefing: Briefing, patient_ref: str) -> dict[str, dict[str, Any]]:
    return {ref: server_resource(ref) for ref in needed_refs(briefing, WRITTEN, patient_ref)}


def document(briefing: Briefing, patient_ref: str) -> dict[str, Any]:
    return build_document(
        briefing,
        WRITTEN,
        resources_for(briefing, patient_ref),
        patient_ref=patient_ref,
        base_url=BASE,
    )


def edit_item(
    briefing: Briefing, section: BriefingSectionId, index: int, **update: Any
) -> Briefing:
    sections = []
    for s in briefing.sections:
        if s.id is section:
            changed = list(s.items)
            changed[index] = changed[index].model_copy(update=update)
            s = s.model_copy(update={"items": changed})
        sections.append(s)
    return briefing.model_copy(update={"sections": sections})


def test_the_document_is_a_valid_fhir_document_bundle() -> None:
    briefing, patient = load("chest-pain")
    bundle = document(briefing, patient)
    Bundle.model_validate(bundle)
    assert bundle["type"] == "document"
    assert bundle["identifier"] == {"system": DOCUMENT_SYSTEM, "value": "ses_chest_pain"}
    assert bundle["timestamp"] == "2026-09-27T09:09:00Z"
    composition = bundle["entry"][0]["resource"]
    assert composition["resourceType"] == "Composition"
    assert composition["status"] == "final"
    assert composition["type"]["coding"] == [
        {
            "system": "http://loinc.org",
            "code": "54094-8",
            "display": "Emergency Department Triage Note",
        }
    ]
    assert composition["subject"] == {"reference": f"{BASE}/Patient/persona-chest-pain"}
    assert composition["encounter"] == {"reference": f"{BASE}/Encounter/enc-1"}
    assert [s["title"] for s in composition["section"]] == [s.title for s in briefing.sections]


def test_every_reference_resolves_inside_the_bundle() -> None:
    briefing, patient = load("chest-pain")
    bundle = document(briefing, patient)
    full_urls = {entry["fullUrl"] for entry in bundle["entry"]}
    composition = bundle["entry"][0]["resource"]
    absolute = [
        ref
        for ref in re.findall(r'"reference": "([^"]+)"', json.dumps(composition))
        if ref.startswith(("http", "urn:uuid:"))
    ]
    assert absolute and set(absolute) <= full_urls
    for entry in bundle["entry"][1:]:
        for ref in references_in(entry["resource"]):
            assert f"{BASE}/{ref}" in full_urls, ref


def test_sections_list_the_resources_behind_them() -> None:
    briefing, patient = load("chest-pain")
    sections = {
        s["title"]: s for s in document(briefing, patient)["entry"][0]["resource"]["section"]
    }
    refs = {
        title: [e["reference"].removeprefix(f"{BASE}/") for e in s.get("entry", [])]
        for title, s in sections.items()
    }
    assert refs["Alerts and triage"] == ["Flag/flag-1", "Task/task-1", "Observation/obs-level"]
    assert "Observation/obs-news2" in refs["Vitals and NEWS2"]
    assert refs["Record (EHR)"] == [
        "Condition/persona-chest-pain-condition-1",
        "MedicationStatement/persona-chest-pain-medication-1",
    ]
    assert refs["Model output"] == [] and refs["Data quality"] == []


def test_every_narrative_line_carries_its_provenance_tag() -> None:
    briefing, patient = load("chest-pain")
    composition = document(briefing, patient)["entry"][0]["resource"]
    for section, body in zip(briefing.sections, composition["section"], strict=True):
        div = body["text"]["div"]
        assert div.startswith('<div xmlns="http://www.w3.org/1999/xhtml">')
        assert div.count("<li>") == len(section.items) + len(section.notes or [])
        assert div.count("<b>[") == len(section.items)
    tags = {t["code"] for t in composition["meta"]["tag"]}
    assert tags == {"EHR", "PATIENT_STATED", "DEVICE_MEASURED", "NURSE_CONFIRMED", "MODEL_INFERRED"}


def test_patient_text_is_escaped_in_the_narrative() -> None:
    briefing, patient = load("chest-pain")
    attack = 'Chief complaint: <script>alert("x")</script> & <img src=x onerror=alert(1)>.'
    briefing = edit_item(briefing, BriefingSectionId.presenting_complaint, 2, text=attack)
    bundle = document(briefing, patient)
    Bundle.model_validate(bundle)
    div = bundle["entry"][0]["resource"]["section"][1]["text"]["div"]
    assert "<script" not in div and "<img" not in div
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; &lt;img src=x" in div


def test_a_source_missing_line_is_marked_in_red() -> None:
    briefing, patient = load("chest-pain")
    briefing = edit_item(briefing, BriefingSectionId.vitals_news2, 0, source_missing=True)
    div = document(briefing, patient)["entry"][0]["resource"]["section"][3]["text"]["div"]
    assert '<span style="color:#b00020;font-weight:bold">source missing</span></li>' in div
    assert div.count("source missing") == 1


def test_ehr_copies_carry_the_ehr_tag_and_the_author_is_a_careflow_device() -> None:
    briefing, patient = load("chest-pain")
    entries = document(briefing, patient)["entry"]
    by_type = {e["resource"]["resourceType"]: e["resource"] for e in entries}
    for kind in ("Patient", "Condition", "MedicationStatement"):
        assert {"system": PROVENANCE_TAGS, "code": "EHR"} in by_type[kind]["meta"]["tag"]
    assert "tag" not in by_type["Observation"]["meta"]  # written by CareFlow with its own tags
    device = entries[1]["resource"]
    assert device["resourceType"] == "Device"
    assert entries[0]["resource"]["author"][0]["reference"] == entries[1]["fullUrl"]
    assert [v["type"]["text"] for v in device["version"]] == ["safety ruleset", "urgency model"]


def test_a_child_without_ehr_conditions_still_builds() -> None:
    briefing, patient = load("child-drowsy")
    bundle = build_document(
        briefing,
        WRITTEN[:1],
        {ref: server_resource(ref) for ref in needed_refs(briefing, WRITTEN[:1], patient)},
        patient_ref=patient,
        base_url=BASE,
    )
    Bundle.model_validate(bundle)
    record = bundle["entry"][0]["resource"]["section"][4]
    assert "entry" not in record
    assert "The EHR lists no allergies, conditions or medications." in record["text"]["div"]


@respx.mock
async def test_write_document_posts_once_with_if_none_exist() -> None:
    briefing, patient = load("chest-pain")
    for ref in needed_refs(briefing, WRITTEN, patient):
        respx.get(f"{BASE}/{ref}").mock(return_value=httpx.Response(200, json=server_resource(ref)))
    stored = {"resourceType": "Bundle", "id": "doc-1", "type": "document"}
    post = respx.post(f"{BASE}/Bundle").mock(
        side_effect=[httpx.Response(201, json=stored), httpx.Response(200, json=stored)]
    )
    async with FhirClient(BASE, retry_delays=()) as client:
        first = await write_document(client, briefing, patient_ref=patient, written=WRITTEN)
        again = await write_document(client, briefing, patient_ref=patient, written=WRITTEN)
    assert (first.status, first.bundle_id, first.created) == (WritebackStatus.ok, "doc-1", True)
    assert (again.status, again.bundle_id, again.created) == (WritebackStatus.ok, "doc-1", False)
    assert post.call_count == 2
    sent = post.calls[0].request
    assert sent.headers["If-None-Exist"] == document_match("ses_chest_pain")
    assert sent.headers["If-None-Exist"] == "identifier=urn:careflow:briefing|ses_chest_pain"
    assert json.loads(sent.content)["entry"][0]["resource"]["resourceType"] == "Composition"


@pytest.mark.parametrize(
    ("patient_ref", "written", "reason"),
    [
        (None, WRITTEN, "no EHR patient to write to"),
        ("Patient/persona-chest-pain", [], "the triage is not written back yet"),
    ],
)
async def test_nothing_to_attach_the_document_to_skips_it(
    patient_ref: str | None, written: list[WrittenResource], reason: str
) -> None:
    briefing, _ = load("chest-pain")
    async with FhirClient(BASE) as client:
        outcome = await write_document(client, briefing, patient_ref=patient_ref, written=written)
    assert (outcome.status, outcome.error) == (WritebackStatus.skipped, reason)


@respx.mock
async def test_a_fhir_error_fails_the_document_without_raising() -> None:
    briefing, patient = load("chest-pain")
    respx.get(url__regex=rf"{re.escape(BASE)}/.*").mock(return_value=httpx.Response(503))
    async with FhirClient(BASE, retry_delays=()) as client:
        outcome = await write_document(client, briefing, patient_ref=patient, written=WRITTEN)
    assert (outcome.status, outcome.error) == (WritebackStatus.failed, "HTTP 503")
