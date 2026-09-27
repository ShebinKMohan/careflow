"""The doctor briefing as a FHIR R4 document Bundle (M1-16).

`build_document` is pure: the briefing, the session's write-back (`fhir_writebacks`, M1-09) and
the resources read from the server become a Bundle of `type` document, identified by
`urn:careflow:briefing|<session_id>`:

- the first entry is the Composition: `status` final, `type` LOINC 54094-8 (mapping.yaml
  `briefing`), the subject and the encounter, the date, a CareFlow Device as author, a title,
  and one section per briefing section. Each section has an XHTML narrative with one line per
  item (its provenance tag, then the text; every value `html.escape`d; a line whose source
  failed the check is marked "source missing" in red) and entries for the resources behind it;
- the Device, then every resource the Composition references and the resources those point to,
  as the server holds them (EHR copies tagged `EHR`, ADR-011).

`write_document` reads those resources, builds the Bundle, checks it against the pinned FHIR
models and posts it once with `If-None-Exist`, so posting it again stores nothing new. Without an
EHR patient or a write-back there is nothing to attach it to, and it is skipped.
"""

from __future__ import annotations

import html
import logging
import re
import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from fhir.resources.R4B.bundle import Bundle

from careflow_contracts.api_models import Briefing, BriefingSection, BriefingSectionId
from careflow_contracts.generated.fact_schema import FhirSource, Provenance
from careflow_integration.fhir.client import FhirClient, FhirError
from careflow_integration.fhir.writeback import codes
from careflow_integration.fhir.writeback.builders import (
    DEVICE_SYSTEM,
    ENCOUNTER,
    NAMESPACE,
    instant,
    meta,
)
from careflow_integration.fhir.writeback.ports import WritebackStatus, WrittenResource

logger = logging.getLogger("careflow_integration.fhir")

type Json = dict[str, Any]

DOCUMENT_SYSTEM: Final = "urn:careflow:briefing"
DEVICE_ID: Final = "careflow-briefing"
TITLE: Final = "CareFlow doctor briefing (research prototype, synthetic data)"
XHTML: Final = "http://www.w3.org/1999/xhtml"
MISSING_STYLE: Final = "color:#b00020;font-weight:bold"
TAGS: Final = {
    Provenance.EHR: "[EHR]",
    Provenance.PATIENT_STATED: "[Patient Stated]",
    Provenance.DEVICE_MEASURED: "[Device]",
    Provenance.NURSE_CONFIRMED: "[Nurse]",
    Provenance.MODEL_INFERRED: "[Model inferred]",
}
#: The write-back resources (by M1-09 local id) behind each section.
SECTION_LOCAL_IDS: Final[dict[BriefingSectionId, tuple[str, ...]]] = {
    BriefingSectionId.alerts_triage: ("flag-*", "task-*", "obs-triage-level"),
    BriefingSectionId.presenting_complaint: ("qr-patient", "obs-pain-score"),
    BriefingSectionId.danger_screens: ("qr-patient", "qr-nurse"),
    BriefingSectionId.vitals_news2: (
        "obs-heart-rate",
        "obs-respiratory-rate",
        "obs-spo2",
        "obs-temperature",
        "obs-blood-pressure",
        "obs-acvpu",
        "obs-supplemental-o2",
        "obs-news2",
    ),
}
#: Resource types copied from the hospital's record (tagged EHR), not written by CareFlow.
EHR_TYPES: Final = frozenset(
    {"Patient", "Condition", "AllergyIntolerance", "MedicationStatement", "MedicationRequest"}
)
MAX_RESOURCES: Final = 200
REFERENCE: Final = re.compile(r"^(?P<type>[A-Z][A-Za-z]+)/(?P<id>[A-Za-z0-9\-.]{1,64})$")


def document_match(session_id: str) -> str:
    """`If-None-Exist` for the session's document."""
    return f"identifier={DOCUMENT_SYSTEM}|{session_id}"


def local_uuid(session_id: str, part: str) -> str:
    return f"urn:uuid:{uuid.uuid5(NAMESPACE, f'{DOCUMENT_SYSTEM}|{session_id}|{part}')}"


def _matches(local_id: str, pattern: str) -> bool:
    return local_id.startswith(pattern[:-1]) if pattern.endswith("*") else local_id == pattern


def section_refs(section: BriefingSection, written: Sequence[WrittenResource]) -> list[str]:
    """`Type/id` of every resource behind a section: the write-back resources it summarises and
    the EHR resources its items cite."""
    refs: list[str] = []
    for pattern in SECTION_LOCAL_IDS.get(section.id, ()):
        refs += [
            f"{w.resource_type}/{w.resource_id}" for w in written if _matches(w.local_id, pattern)
        ]
    for item in section.items:
        if isinstance(item.source, FhirSource):
            refs.append(f"{item.source.resource_type}/{item.source.id}")
    return list(dict.fromkeys(refs))


def encounter_ref(written: Sequence[WrittenResource]) -> str | None:
    found = next((w for w in written if w.local_id == ENCOUNTER), None)
    return f"Encounter/{found.resource_id}" if found else None


def needed_refs(
    briefing: Briefing, written: Sequence[WrittenResource], patient_ref: str
) -> list[str]:
    """Every resource the Composition references, subject and encounter first."""
    refs = [patient_ref]
    if (encounter := encounter_ref(written)) is not None:
        refs.append(encounter)
    for section in briefing.sections:
        refs += section_refs(section, written)
    return list(dict.fromkeys(refs))


def references_in(node: Any) -> Iterator[str]:
    """Every relative `Type/id` reference inside a resource."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "reference" and isinstance(value, str) and REFERENCE.match(value):
                yield value
            else:
                yield from references_in(value)
    elif isinstance(node, list):
        for value in node:
            yield from references_in(value)


def narrative(section: BriefingSection) -> Json:
    """One line per item: its provenance tag and its text, escaped; notes after the items."""
    lines: list[str] = []
    for item in section.items:
        tag = html.escape(TAGS[item.provenance])
        text = html.escape(item.text)
        missing = (
            f' <span style="{MISSING_STYLE}">source missing</span>' if item.source_missing else ""
        )
        lines.append(f"<li><b>{tag}</b> {text}{missing}</li>")
    lines += [f"<li><i>{html.escape(note.text)}</i></li>" for note in section.notes or []]
    body = f"<ul>{''.join(lines)}</ul>" if lines else "<p>Nothing recorded.</p>"
    title = html.escape(section.title)
    return {"status": "generated", "div": f'<div xmlns="{XHTML}"><h3>{title}</h3>{body}</div>'}


def device(briefing: Briefing) -> Json:
    """CareFlow as the author: a Device inside the document only, never stored on its own."""
    versions = briefing.versions
    found = (
        ("safety ruleset", versions.ruleset),
        ("urgency model", versions.model),
        ("summary prompt", versions.prompt),
    )
    return {
        "resourceType": "Device",
        "identifier": [{"system": DEVICE_SYSTEM, "value": DEVICE_ID}],
        "deviceName": [{"name": "CareFlow briefing builder", "type": "model-name"}],
        "version": [{"type": {"text": name}, "value": value} for name, value in found if value],
    }


def with_ehr_tag(resource: Json) -> Json:
    """A copy of an EHR resource with the EHR provenance tag added (ADR-011)."""
    tag = {"system": codes.entry("write_batch.meta_tag", "(every written resource)")["code_system"]}
    tag["code"] = Provenance.EHR.value
    current = resource.get("meta", {})
    tags = [t for t in current.get("tag", []) if t != tag]
    return {**resource, "meta": {**current, "tag": [*tags, tag]}}


def build_document(
    briefing: Briefing,
    written: Sequence[WrittenResource],
    resources: Mapping[str, Json],
    *,
    patient_ref: str,
    base_url: str,
) -> Json:
    """The document Bundle. `resources` holds every needed resource by `Type/id`."""
    base = base_url.rstrip("/")
    session_id = briefing.session_id

    def absolute(ref: str) -> str:
        return f"{base}/{ref}"

    device_url = local_uuid(session_id, "device")
    loinc = codes.entry("briefing", "Composition")
    composition: Json = {
        "resourceType": "Composition",
        "meta": meta(item.provenance for s in briefing.sections for item in s.items),
        "identifier": {"system": DOCUMENT_SYSTEM, "value": session_id},
        "status": "final",
        "type": {
            "coding": [
                {"system": loinc["code_system"], "code": loinc["code"], "display": loinc["display"]}
            ],
            "text": loinc["display"],
        },
        "subject": {"reference": absolute(patient_ref)},
        "date": instant(briefing.generated_at),
        "author": [{"reference": device_url, "display": "CareFlow briefing builder"}],
        "title": TITLE,
    }
    if (encounter := encounter_ref(written)) is not None:
        composition["encounter"] = {"reference": absolute(encounter)}
    sections = []
    for section in briefing.sections:
        body: Json = {"title": section.title, "text": narrative(section)}
        refs = [ref for ref in section_refs(section, written) if ref in resources]
        if refs:
            body["entry"] = [{"reference": absolute(ref)} for ref in refs]
        sections.append(body)
    composition["section"] = sections
    entries: list[Json] = [
        {"fullUrl": local_uuid(session_id, "composition"), "resource": composition},
        {"fullUrl": device_url, "resource": device(briefing)},
    ]
    for ref, resource in resources.items():
        copy = with_ehr_tag(resource) if resource["resourceType"] in EHR_TYPES else resource
        entries.append({"fullUrl": absolute(ref), "resource": copy})
    return {
        "resourceType": "Bundle",
        "identifier": {"system": DOCUMENT_SYSTEM, "value": session_id},
        "type": "document",
        "timestamp": instant(briefing.generated_at),
        "entry": entries,
    }


async def read_resources(client: FhirClient, refs: Iterable[str]) -> dict[str, Json]:
    """The resources, and every resource they reference in turn (up to MAX_RESOURCES)."""
    pending = list(dict.fromkeys(refs))
    found: dict[str, Json] = {}
    while pending:
        ref = pending.pop(0)
        if ref in found:
            continue
        if len(found) >= MAX_RESOURCES:
            raise FhirError(f"the document would hold more than {MAX_RESOURCES} resources")
        resource_type, resource_id = ref.split("/", 1)
        resource = await client.read(resource_type, resource_id)
        found[ref] = resource
        pending += [r for r in references_in(resource) if r not in found]
    return found


@dataclass(frozen=True)
class DocumentOutcome:
    status: WritebackStatus
    bundle_id: str | None = None
    #: Stored by this call (201), rather than found already there (200).
    created: bool = False
    #: Short reason for a failed or skipped write; never patient data.
    error: str | None = None


async def write_document(
    client: FhirClient,
    briefing: Briefing,
    *,
    patient_ref: str | None,
    written: Sequence[WrittenResource],
) -> DocumentOutcome:
    """Build the document from the server's resources and post it once, idempotently."""
    if patient_ref is None:
        return DocumentOutcome(WritebackStatus.skipped, error="no EHR patient to write to")
    if encounter_ref(written) is None:
        return DocumentOutcome(WritebackStatus.skipped, error="the triage is not written back yet")
    try:
        resources = await read_resources(client, needed_refs(briefing, written, patient_ref))
        bundle = build_document(
            briefing, written, resources, patient_ref=patient_ref, base_url=client.base_url
        )
        Bundle.model_validate(bundle)
        stored = await client.create(bundle, if_none_exist=document_match(briefing.session_id))
    except FhirError as exc:
        logger.warning("fhir.briefing_document", extra={"status": "failed", "error": str(exc)})
        return DocumentOutcome(WritebackStatus.failed, error=str(exc)[:200])
    except ValueError as exc:  # a builder bug: the Bundle does not fit the FHIR models
        logger.error("fhir.briefing_document", extra={"status": "failed", "error": "invalid"})
        return DocumentOutcome(
            WritebackStatus.failed, error=f"invalid document ({type(exc).__name__})"
        )
    logger.info(
        "fhir.briefing_document",
        extra={"status": "ok", "bundle_id": stored.resource_id, "created": stored.created},
    )
    return DocumentOutcome(WritebackStatus.ok, bundle_id=stored.resource_id, created=stored.created)


__all__ = [
    "DOCUMENT_SYSTEM",
    "SECTION_LOCAL_IDS",
    "DocumentOutcome",
    "build_document",
    "document_match",
    "narrative",
    "needed_refs",
    "read_resources",
    "section_refs",
    "write_document",
]
