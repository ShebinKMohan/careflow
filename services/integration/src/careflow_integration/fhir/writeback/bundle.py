"""`build_transaction`: the write-back as one deterministic transaction Bundle of conditional
creates, and `written_resources`: what the server's transaction-response says it holds (M1-09)."""

from __future__ import annotations

import re
import uuid
from typing import Any, Final

from fhir.resources.R4B.bundle import Bundle

from careflow_integration.adapters.loader import HospitalAdapter
from careflow_integration.fhir.client import FhirError
from careflow_integration.fhir.writeback.builders import (
    ENCOUNTER,
    NAMESPACE,
    PROVENANCE,
    SESSION_SYSTEM,
    Ids,
    build_resources,
)
from careflow_integration.fhir.writeback.ports import WrittenResource
from careflow_integration.fhir.writeback.record import TriageRecord

type Json = dict[str, Any]

BUNDLE_SYSTEM: Final = "urn:careflow:writeback-bundle"
LOCATION: Final = re.compile(
    r"(?P<type>[A-Z][A-Za-z]+)/(?P<id>[A-Za-z0-9\-.]{1,64})"
    r"(?:/_history/(?P<version>[A-Za-z0-9\-.]{1,64}))?/?$"
)


def bundle_id(record: TriageRecord) -> str:
    """The same for every retry of one decision's write-back."""
    key = f"{BUNDLE_SYSTEM}|{record.session_id}|{record.decision.decision_id}"
    return str(uuid.uuid5(NAMESPACE, key))


def build_transaction(record: TriageRecord, adapter: HospitalAdapter) -> Json:
    """Every resource as a conditional create (`ifNoneExist` on its identifier or tag), with a
    `urn:uuid` fullUrl derived from that identifier: the same record builds the same Bundle, and
    posting it again creates nothing new."""
    ids = Ids(record.session_id)
    bundle: Json = {
        "resourceType": "Bundle",
        "identifier": {"system": BUNDLE_SYSTEM, "value": bundle_id(record)},
        "type": "transaction",
        "entry": [
            {
                "fullUrl": ids.full_url(built.local_id),
                "resource": built.resource,
                "request": {
                    "method": "POST",
                    "url": built.resource["resourceType"],
                    "ifNoneExist": ids.match(built.local_id),
                },
            }
            for built in build_resources(record, adapter)
        ],
    }
    Bundle.model_validate(bundle)
    return bundle


def local_id(entry: Json) -> str:
    """The local id of a Bundle entry, read back from its conditional-create match."""
    match = str(entry["request"]["ifNoneExist"])
    if match.startswith("_tag="):
        return PROVENANCE
    system, value = match.removeprefix("identifier=").split("|", 1)
    return ENCOUNTER if system == SESSION_SYSTEM else value.rsplit(":", 1)[1]


def written_resources(bundle: Json, response: Json) -> list[WrittenResource]:
    """Pair each request with its transaction-response entry (same order, per the FHIR spec)."""
    answers = response.get("entry", [])
    if response.get("type") != "transaction-response" or len(answers) != len(bundle["entry"]):
        raise FhirError("unexpected transaction-response")
    written: list[WrittenResource] = []
    for request, answer in zip(bundle["entry"], answers, strict=True):
        reply = answer.get("response", {})
        found = LOCATION.search(str(reply.get("location", "")))
        if found is None or found["type"] != request["resource"]["resourceType"]:
            raise FhirError("unreadable location in the transaction-response")
        written.append(
            WrittenResource(
                local_id=local_id(request),
                resource_type=found["type"],
                resource_id=found["id"],
                version_id=found["version"],
                created=str(reply.get("status", "")).startswith("201"),
            )
        )
    return written


__all__ = ["BUNDLE_SYSTEM", "build_transaction", "bundle_id", "local_id", "written_resources"]
