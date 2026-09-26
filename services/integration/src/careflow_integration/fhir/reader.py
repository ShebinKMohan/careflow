"""`FhirEhrReader.fetch_context`: a patient's EHR context over FHIR R4 (M1-08), implementing the
intake's `EhrReader` port.

The patient is found by identifier (one match; none is `not_found`, several are `ambiguous`), then
allergies, conditions, medications and upcoming appointments are searched together. Some searches
failing is `partial`; all failing, or the 8 s budget running out, is `unavailable`. A circuit
breaker answers `unavailable` at once for 30 s after 3 unavailable results in a row. Nothing here
raises for an EHR problem: the intake goes on and the nurse sees the status.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

import httpx

from careflow_contracts.generated.ehr_fetch_schema import EhrFetch, EhrStatus, PatientIdentifier
from careflow_contracts.generated.structured_facts_schema import EhrContext
from careflow_integration.adapters import mapper
from careflow_integration.adapters.loader import HospitalAdapter, load_adapter
from careflow_integration.fhir.client import FhirClient, FhirError

logger = logging.getLogger("careflow_integration.fhir")

type Json = dict[str, Any]

FAILURES_TO_OPEN: Final = 3
OPEN_SECONDS: Final = 30.0


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class CircuitBreaker:
    """Opens after `threshold` failures in a row; while open, calls are refused for `cooldown_s`.
    After the cooldown calls go through again: a success closes it, and a failure opens it again at
    once (the failures in a row are not reset until a success)."""

    threshold: int = FAILURES_TO_OPEN
    cooldown_s: float = OPEN_SECONDS
    clock: Callable[[], float] = time.monotonic
    failures: int = 0
    opened_at: float | None = field(default=None)

    @property
    def is_open(self) -> bool:
        return self.opened_at is not None and self.clock() - self.opened_at < self.cooldown_s

    def allow(self) -> bool:
        return not self.is_open

    def record(self, *, ok: bool) -> None:
        if ok:
            self.failures, self.opened_at = 0, None
            return
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = self.clock()


class FhirEhrReader:
    def __init__(
        self,
        adapter: HospitalAdapter | None = None,
        *,
        client: FhirClient | None = None,
        breaker: CircuitBreaker | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.adapter = adapter or load_adapter()
        timeouts = self.adapter.timeouts
        self.client = client or FhirClient(
            self.adapter.fhir_base_url,
            timeout=httpx.Timeout(timeouts.read_s, connect=timeouts.connect_s),
        )
        self.breaker = breaker or CircuitBreaker()
        self.clock = clock

    async def fetch_context(self, identifier: PatientIdentifier, clinic_id: str) -> EhrFetch:
        now = self.clock()
        if not self.breaker.allow():
            return self._unavailable(now, "circuit open after repeated EHR failures")
        try:
            result = await asyncio.wait_for(
                self._fetch(identifier, now), timeout=self.adapter.timeouts.total_s
            )
        except TimeoutError:
            result = self._unavailable(now, f"no answer within {self.adapter.timeouts.total_s:g} s")
        except FhirError as exc:
            result = self._unavailable(now, f"Patient search: {exc}")
        self.breaker.record(ok=result.status is not EhrStatus.unavailable)
        logger.info("fhir.fetch", extra={"status": result.status.value, "clinic_id": clinic_id})
        return result

    @staticmethod
    def _unavailable(now: datetime, reason: str) -> EhrFetch:
        return EhrFetch(status=EhrStatus.unavailable, fetched_at=now, errors=[reason])

    async def _fetch(self, identifier: PatientIdentifier, now: datetime) -> EhrFetch:
        query = {"identifier": f"{identifier.system}|{identifier.value}"}
        matches = await self.client.search("Patient", query)
        found = [parsed for raw in matches if (parsed := mapper.parse(raw)) is not None]
        if len(found) != 1:
            status = EhrStatus.not_found if not found else EhrStatus.ambiguous
            return EhrFetch(status=status, fetched_at=now, errors=[f"{len(found)} patients match"])
        patient = found[0]
        pid, today = patient["id"], now.date().isoformat()
        searches: dict[str, tuple[str, dict[str, str]]] = {
            "AllergyIntolerance": (
                "AllergyIntolerance",
                {"patient": pid, "clinical-status": "active"},
            ),
            "Condition": ("Condition", {"patient": pid, "clinical-status": "active"}),
            **{
                kind: (kind, {"patient": pid, "status": "active"})
                for kind in self.adapter.medication_types
            },
            "Appointment": ("Appointment", {"patient": pid, "date": f"ge{today}"}),
        }
        results = await asyncio.gather(
            *(self.client.search(kind, params) for kind, params in searches.values()),
            return_exceptions=True,
        )
        errors: list[str] = []
        resources: dict[str, list[Json]] = {}
        for name, result in zip(searches, results, strict=True):
            if isinstance(result, BaseException):
                if not isinstance(result, FhirError):
                    raise result  # a bug, not an EHR problem
                errors.append(f"{name}: {result}")
                continue
            resources[name] = [
                parsed for raw in result if (parsed := mapper.parse(raw)) is not None
            ]
        if not resources:
            return EhrFetch(status=EhrStatus.unavailable, fetched_at=now, errors=errors)
        medications = [r for kind in self.adapter.medication_types for r in resources.get(kind, [])]
        context = EhrContext(
            allergies=mapper.allergies(resources.get("AllergyIntolerance", []), self.adapter, now),
            conditions=mapper.conditions(resources.get("Condition", []), self.adapter, now),
            medications=mapper.medications(medications, self.adapter, now),
        )
        return EhrFetch(
            status=EhrStatus.partial if errors else EhrStatus.ok,
            patient_ref=f"Patient/{pid}",
            patient=mapper.patient(patient, now),
            context=context,
            appointments=mapper.appointments(resources.get("Appointment", []), self.adapter),
            fetched_at=now,
            errors=errors,
        )


__all__ = ["FAILURES_TO_OPEN", "OPEN_SECONDS", "CircuitBreaker", "FhirEhrReader"]
