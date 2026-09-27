"""The briefing's FHIR document against the seeded HAPI server (M1-16).

    make up && uv run pytest services/integration -m hapi -k briefing

The chest-pain triage is written back under a fresh session id (M1-09), then the chest-pain
briefing is written as a document Bundle for that session: HAPI's `$validate` reports no error
or fatal issue, and posting it twice stores one Bundle. Everything the test created is deleted
at the end. Skipped when HAPI does not answer (conftest).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel

from careflow_contracts.api_models import Briefing
from careflow_contracts.generated.events.triage_confirmed_schema import TriageConfirmedData
from careflow_integration.adapters.loader import load_adapter
from careflow_integration.fhir.client import FhirClient
from careflow_integration.fhir.writeback.composition import (
    DOCUMENT_SYSTEM,
    build_document,
    needed_refs,
    read_resources,
    write_document,
)
from careflow_integration.fhir.writeback.handler import WritebackHandler
from careflow_integration.fhir.writeback.ports import (
    MemoryWritebackStore,
    WritebackStatus,
    WrittenResource,
)
from careflow_integration.fhir.writeback.record import TriageRecord

pytestmark = pytest.mark.hapi

BRIEFING = Path(__file__).parent / "fixtures" / "briefing" / "chest-pain.json"


class Records:
    def __init__(self, record: TriageRecord) -> None:
        self.record = record

    async def load(self, clinic_id: str, session_id: str) -> TriageRecord | None:
        return self.record


class Events:
    async def publish(self, event_type: str, data: BaseModel) -> None:
        return None


async def no_sleep(_: float) -> None:
    return None


def fresh(record: TriageRecord) -> TriageRecord:
    sid = f"ses_hapi_{uuid.uuid4().hex[:12]}"
    decision = record.decision.model_copy(update={"session_id": sid})
    return record.model_copy(update={"session_id": sid, "decision": decision})


def confirmed(record: TriageRecord) -> TriageConfirmedData:
    d = record.decision
    return TriageConfirmedData(
        clinic_id=record.clinic_id,
        session_id=record.session_id,
        decision_id=d.decision_id,
        suggestion_id=d.suggestion_id,
        action=d.action,
        final_level=d.final_level,
        scale=d.scale,
        nurse_user_id=d.nurse_user_id,
        decided_at=d.decided_at,
    )


async def delete(refs: list[str]) -> None:
    """Test clean-up only: the product's FhirClient has no delete."""
    async with httpx.AsyncClient(base_url=load_adapter().fhir_base_url + "/") as raw:
        for ref in refs:
            await raw.delete(ref)


async def test_the_document_validates_and_is_stored_once(
    triage_record: Callable[[str], TriageRecord],
) -> None:
    record = fresh(triage_record("chest_pain"))
    sid, patient = record.session_id, record.patient_ref
    assert patient is not None
    body = json.loads(BRIEFING.read_text(encoding="utf-8"))
    briefing = Briefing.model_validate(body["briefing"]).model_copy(update={"session_id": sid})
    created: list[str] = []
    async with FhirClient(load_adapter().fhir_base_url) as client:
        handler = WritebackHandler(
            records=Records(record),
            store=MemoryWritebackStore(),
            events=Events(),
            client=client,
            sleep=no_sleep,
        )
        written: list[WrittenResource] = list(
            (await handler.on_triage_confirmed(confirmed(record))).resources
        )
        # Deleted in reverse Bundle order, so no reference is left dangling.
        created += [f"{w.resource_type}/{w.resource_id}" for w in reversed(written)]
        try:
            assert len(written) == 16
            resources = await read_resources(client, needed_refs(briefing, written, patient))
            bundle = build_document(
                briefing, written, resources, patient_ref=patient, base_url=client.base_url
            )
            outcome = await client.validate(bundle)
            issues = [
                i for i in outcome.get("issue", []) if i.get("severity") in ("error", "fatal")
            ]
            assert issues == [], json.dumps(issues, indent=1)[:2000]

            first = await write_document(client, briefing, patient_ref=patient, written=written)
            assert (first.status, first.created) == (WritebackStatus.ok, True), first.error
            created.insert(0, f"Bundle/{first.bundle_id}")
            again = await write_document(client, briefing, patient_ref=patient, written=written)
            assert (again.status, again.created) == (WritebackStatus.ok, False), again.error
            assert again.bundle_id == first.bundle_id

            stored = await client.search("Bundle", {"identifier": f"{DOCUMENT_SYSTEM}|{sid}"})
            assert len(stored) == 1
            composition = stored[0]["entry"][0]["resource"]
            assert composition["resourceType"] == "Composition"
            assert composition["type"]["coding"][0]["code"] == "54094-8"
            assert len(composition["section"]) == 7
        finally:
            await delete(created)
