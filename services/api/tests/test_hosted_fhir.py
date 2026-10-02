"""Conditional writeback and FHIR references survive a fresh transport instance."""

import uuid
from typing import Any

from careflow_api.hosting.fhir import DEMO_FHIR_URL, DemoFhirTransport, matches
from careflow_integration.fhir.client import FhirClient


def test_single_and_repeated_identifiers() -> None:
    identity = {"system": "urn:test", "value": "synthetic"}
    assert matches({"identifier": identity}, {"identifier": "urn:test|synthetic"})
    assert matches({"identifier": [identity]}, {"identifier": "urn:test|synthetic"})
    assert not matches({"identifier": identity}, {"identifier": "urn:other|synthetic"})
    assert not matches({"identifier": identity}, {"unhandled": "anything"})


async def test_conditional_bundle_create_is_durable_and_idempotent(database: Any) -> None:
    value = uuid.uuid4().hex
    bundle = {
        "resourceType": "Bundle",
        "type": "collection",
        "identifier": {"system": "urn:test", "value": value},
        "entry": [],
    }
    async with FhirClient(DEMO_FHIR_URL, transport=DemoFhirTransport()) as client:
        first = await client.create(bundle, if_none_exist=f"identifier=urn:test|{value}")
    async with FhirClient(DEMO_FHIR_URL, transport=DemoFhirTransport()) as client:
        second = await client.create(bundle, if_none_exist=f"identifier=urn:test|{value}")
        stored = await client.read("Bundle", first.resource_id)
    assert first.created and not second.created
    assert first.resource_id == second.resource_id
    assert stored["identifier"]["value"] == value
    assert stored["meta"]["versionId"] == "1"


async def test_transaction_resolves_references_and_rolls_back_on_failure(database: Any) -> None:
    marker = uuid.uuid4().hex
    patient = {"resourceType": "Patient", "identifier": [{"system": "urn:test", "value": marker}]}
    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {
                "fullUrl": "urn:uuid:patient",
                "resource": patient,
                "request": {
                    "method": "POST",
                    "url": "Patient",
                    "ifNoneExist": f"identifier=urn:test|{marker}",
                },
            },
            {
                "resource": {
                    "resourceType": "Encounter",
                    "status": "in-progress",
                    "subject": {"reference": "urn:uuid:patient"},
                },
                "request": {"method": "POST", "url": "Encounter"},
            },
        ],
    }
    async with FhirClient(DEMO_FHIR_URL, transport=DemoFhirTransport()) as client:
        reply = await client.transaction(bundle)
        pid = reply["entry"][0]["resource"]["id"]
        assert reply["entry"][1]["resource"]["subject"]["reference"] == f"Patient/{pid}"
        repeated = await client.transaction(bundle)
        assert repeated["entry"][0]["resource"]["id"] == pid
        assert repeated["entry"][1]["resource"]["subject"]["reference"] == f"Patient/{pid}"
