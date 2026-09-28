"""Own-session patient receipt: sourced facts and a clearly provisional nurse handoff.

This does not broaden access to staff triage, EHR resources, or another patient's record.
Rule identifiers are not hospital emergency announcement codes. A suggestion is never a
confirmed disposition; absence of an alert must never be presented as permission to wait.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from careflow_api.deps import Principal, require_roles
from careflow_api.devices.gateway import latest_vitals
from careflow_api.intake.runtime import IntakeRuntime
from careflow_api.intake.views import warnings
from careflow_api.routers.sessions import own_session, runtime, session_row
from careflow_contracts.api_models import PatientIntakeSummary
from careflow_intake.facts import current_entries
from careflow_intake.state import IntakeState

router = APIRouter(tags=["intake"])


def receipt(state: IntakeState, *, simulated: bool = False) -> PatientIntakeSummary:
    facts = []
    for entry in current_entries(state.get("facts", [])):
        # Raw record identifiers, transcript spans, and caregiver details are deliberately absent.
        raw: Any = entry.fact.model_dump(mode="json")
        value = raw.get("value", raw.get("name", {}).get("value"))
        if isinstance(value, dict):
            value = value.get("display") or value.get("code")
        if value is None:
            continue
        unit = raw.get("unit")
        facts.append(
            {
                "key": entry.key.value,
                "fact_id": entry.fact_id,
                "value": f"{value}{' ' + unit if unit else ''}"[:1000],
                "provenance": entry.provenance.value,
                "simulated": simulated and entry.provenance.value == "DEVICE_MEASURED",
            }
        )
    decision = state.get("nurse_decision")
    body: dict[str, Any] = {
        "session_id": state["session_id"],
        "visit_reference": "CF-" + state["session_id"].replace("-", "")[:8].upper(),
        "nurse_confirmed": decision is not None,
        "assistance_requested": bool(state.get("flags", {}).get("assistance_requested")),
        "facts": facts,
        "alerts": [
            {"code": a.rule_id.value, "severity": a.severity.value} for a in state.get("alerts", [])
        ],
        "suggestion": state.get("suggestion"),
        "warnings": [w.value for w in warnings(state)],
    }
    if decision is not None:
        body.update(confirmed_level=int(decision.final_level), confirmed_scale=decision.scale.value)
    return PatientIntakeSummary.model_validate(body)


@router.get(
    "/v1/sessions/{session_id}/intake-summary",
    response_model=PatientIntakeSummary,
    response_model_exclude_unset=True,
)
async def get_intake_summary(
    session_id: str,
    principal: Annotated[Principal, Depends(require_roles("patient_session"))],
    intake: Annotated[IntakeRuntime, Depends(runtime)],
) -> PatientIntakeSummary:
    own_session(principal, session_id)
    await session_row(principal, session_id)
    state = await intake.state(session_id)
    if state is None:
        raise HTTPException(404, "No intake for this session.")
    latest = latest_vitals.get(session_id)
    return receipt(state, simulated=bool(latest and latest.simulated))
