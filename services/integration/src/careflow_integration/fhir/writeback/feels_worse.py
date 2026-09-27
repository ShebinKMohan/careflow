"""The FHIR `Flag` for an "I feel worse" press (M2-07): one per press, created once.

A clinical Flag on the patient, active from the press, whose text names the danger answers as
codes (never the patient's note). Its identifier `<session>:feels-worse-<press>` makes the create
conditional, so a retry never stores a second copy.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from careflow_contracts import Provenance
from careflow_integration.fhir.client import Created, FhirClient
from careflow_integration.fhir.writeback import codes
from careflow_integration.fhir.writeback.builders import FACT_SYSTEM, MODELS, instant, meta

Json = dict[str, object]


def feels_worse_flag(
    *,
    session_id: str,
    patient_ref: str,
    press_number: int,
    answer_codes: Sequence[str],
    reported_at: datetime,
) -> Json:
    text = "Patient reports feeling worse while waiting"
    if answer_codes:
        text += " (" + ", ".join(answer_codes) + ")"
    flag: Json = {
        "resourceType": "Flag",
        "meta": meta([Provenance.PATIENT_STATED]),
        "identifier": [
            {"system": FACT_SYSTEM, "value": f"{session_id}:feels-worse-{press_number}"}
        ],
        "status": "active",
        "category": [{"coding": [codes.FLAG_CLINICAL]}],
        "code": {"text": text},
        "subject": {"reference": patient_ref},
        "period": {"start": instant(reported_at)},
    }
    MODELS["Flag"].model_validate(flag)
    return flag


async def write_feels_worse_flag(client: FhirClient, flag: Json) -> Created:
    identifier = flag["identifier"][0]  # type: ignore[index]
    search = f"identifier={identifier['system']}|{identifier['value']}"
    return await client.create(flag, if_none_exist=search)


__all__ = ["feels_worse_flag", "write_feels_worse_flag"]
