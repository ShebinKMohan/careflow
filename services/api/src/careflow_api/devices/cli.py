"""`careflow-vitals-sim`: play a scenario into the API like a device gateway.

    careflow-vitals-sim --session ses_123 --scenario septic --api http://localhost:8000 \
        --token "$DEVICE_TOKEN"

Each reading is one `POST /v1/sessions/{session}/vitals` (role kiosk_device). The token is read
from --token or CAREFLOW_DEVICE_TOKEN and is never printed. Readings are simulated.
"""

from __future__ import annotations

import asyncio
import os
from typing import Annotated

import httpx
import typer

from careflow_api.devices.gateway import run_simulation
from careflow_api.devices.scenarios_loader import load_scenario, scenario_names
from careflow_api.devices.simulated import SimulatedSerialDevice
from careflow_contracts.api_models import DeviceVitalsReading

app = typer.Typer(add_completion=False, help="Simulated vitals device (synthetic data only).")


def default_scenario() -> str:
    return os.environ.get("VITALS_SCENARIO") or "normal"


@app.command()
def simulate(
    session: Annotated[str, typer.Option(help="Session id the readings belong to.")],
    scenario: Annotated[
        str, typer.Option(help="normal, septic, hypoxic or tachycardic_child.")
    ] = "",
    api: Annotated[str, typer.Option(help="API base URL.")] = "http://localhost:8000",
    token: Annotated[
        str, typer.Option(help="kiosk_device bearer token.", envvar="CAREFLOW_DEVICE_TOKEN")
    ] = "",
    seed: Annotated[int, typer.Option(help="Noise seed (same seed, same readings).")] = 42,
    speed: Annotated[float, typer.Option(help="1 = real time, 0 = no waiting.")] = 1.0,
    max_readings: Annotated[int, typer.Option(help="Stop after this many (0 = all).")] = 0,
) -> None:
    """Post one reading per frame; exit 1 if the API rejects any."""
    name = scenario or default_scenario()
    if name not in scenario_names():
        raise typer.BadParameter(f"unknown scenario {name!r}; one of {', '.join(scenario_names())}")
    device = SimulatedSerialDevice(load_scenario(name), seed=seed, speed=speed)
    rejected = asyncio.run(_post_all(device, session, api, token, max_readings or None))
    if rejected:
        typer.echo(f"careflow-vitals-sim: {rejected} reading(s) rejected", err=True)
        raise typer.Exit(1)


async def _post_all(
    device: SimulatedSerialDevice, session: str, api: str, token: str, max_readings: int | None
) -> int:
    rejected = 0
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    url = f"{api.rstrip('/')}/v1/sessions/{session}/vitals"
    async with httpx.AsyncClient(headers=headers, timeout=10) as client:

        async def post(reading: DeviceVitalsReading) -> None:
            nonlocal rejected
            body = reading.model_dump(mode="json", exclude_none=True)
            response = await client.post(url, json=body)
            ok = response.status_code == 200
            rejected += 0 if ok else 1
            fields = sorted(k for k in body if k not in {"device_id", "reading_id", "measured_at"})
            typer.echo(
                f"{reading.reading_id} {'accepted' if ok else f'rejected ({response.status_code})'}"
                f" {' '.join(f'{k}={body[k]}' for k in fields)}"
            )

        sent = await run_simulation(device, post, max_readings=max_readings)
    summary = f"{device.scenario.id}, {sent} readings, {sent - rejected} accepted (simulated)"
    typer.echo(f"careflow-vitals-sim: {summary}")
    return rejected


def main() -> None:
    app()
