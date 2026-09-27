"""Development-only test hooks (/v1/dev/*). Registered only when CAREFLOW_ENV=dev (ADR-018)."""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Request, status

from careflow_api.config import Settings, get_settings
from careflow_api.deps import require_roles
from careflow_api.devices import SimulatedSerialDevice, load_scenario, scenario_names
from careflow_api.devices.gateway import VitalsSink, latest_vitals, run_simulation
from careflow_api.routers.sessions import device_facts
from careflow_contracts.api_models import (
    DeviceVitalsReading,
    VitalsScenario,
    VitalsSimRequest,
    VitalsSimStarted,
)

router = APIRouter(prefix="/v1/dev", tags=["dev"])

SessionId = Annotated[
    str, Path(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
]


def chosen_scenario(body: VitalsSimRequest | None) -> str:
    """Request body, then VITALS_SCENARIO, then normal."""
    if body is not None and body.scenario is not None:
        return body.scenario.value
    name = os.environ.get("VITALS_SCENARIO") or "normal"
    if name not in scenario_names():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unknown VITALS_SCENARIO."
        )
    return name


def session_sink(request: Request, session_id: str) -> VitalsSink:
    """Each simulated reading is kept for the kiosk's vitals panel and, when the session has an
    intake (M1-12), becomes DEVICE_MEASURED facts with the safety rules run at once."""
    latest = latest_vitals.sink(session_id)
    intake = getattr(request.app.state, "intake", None)

    async def put(reading: DeviceVitalsReading) -> None:
        await latest(reading)
        if intake is not None and await intake.state(session_id) is not None:
            await intake.add_facts_and_evaluate(session_id, device_facts(session_id, reading))

    return put


@router.post(
    "/sessions/{session_id}/vitals-sim",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=VitalsSimStarted,
    dependencies=[Depends(require_roles("kiosk_device"))],
)
async def start_vitals_simulation(
    session_id: SessionId,
    background: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
    request: Request,
    body: VitalsSimRequest | None = None,
) -> VitalsSimStarted:
    name = chosen_scenario(body)
    device = SimulatedSerialDevice(load_scenario(name), speed=settings.vitals_sim_speed)
    background.add_task(run_simulation, device, session_sink(request, session_id))
    return VitalsSimStarted(
        session_id=session_id,
        scenario=VitalsScenario(name),
        frames=device.frame_count,
        simulated=True,
    )
