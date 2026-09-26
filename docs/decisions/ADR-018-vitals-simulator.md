# ADR-018: Scenario-driven vitals simulator

## Status

Accepted, 27 Sep 2026 ([M1-03]).

## Context

There is no vitals hardware, but the sepsis, hypoxia and child rules need device readings. The simulator has to behave like a serial monitor, be deterministic for tests, and feed the kiosk's vitals panel before the real vitals handler and session status exist (M1-12) and before live updates (M1-11). Two points in the issue text differ from the contract: the CLI takes a patient-session token, while the contract gives `POST /v1/sessions/{id}/vitals` to `kiosk_device` only; and `SessionStatus` has no field for readings.

## Decision

- **Device path:** `SimulatedSerialDevice` interpolates the scenario series, adds seeded Gaussian noise (`random.Random(seed)`, default 42), encodes each point as an ASCII frame (`HR=112;SPO2=93;TEMP=38.6;SBP=;DBP=\n`) and parses it back with `parse_frame`, so the parser always runs. `parse_frame` rejects partial lines, non-ASCII bytes, unknown, repeated or missing fields, non-numbers, and values outside plausible ranges (HR 20-300, SpO2 50-100, temperature 25-45 °C, SBP 40-300, DBP 20-200). One frame per scenario second, paced by `speed` (0 = no waiting).
- **Blood pressure** is sent only when the scenario has `bp_cuff: true` and `SIM_BP_CUFF=1`. The kiosk measures pulse, SpO2 and temperature; the nurse takes blood pressure. The child scenario has no cuff.
- **Gateway:** each frame becomes one `DeviceVitalsReading` (`reading_id` `rd_0001`...). `careflow-vitals-sim` POSTs readings to `/v1/sessions/{id}/vitals` with a **kiosk_device** token, because the contract wins. The token comes from `--token` or `CAREFLOW_DEVICE_TOKEN` and is never printed.
- **Dev endpoint:** `POST /v1/dev/sessions/{session_id}/vitals-sim` (tag `dev`, role `kiosk_device`) is added to the contract, and the router is registered only when `CAREFLOW_ENV=dev` (default `prod`), so the route answers 404 everywhere else. The scenario comes from the request body, then `VITALS_SCENARIO`, then `normal`; the kiosk puts its `?scenario=` in the body. The simulator runs as a background task into an in-memory latest-reading store, until M1-12's vitals handler and status route replace that store.
- **Readings for the patient:** `SessionStatus` gains the optional `vitals` (`LatestVitals`: numbers, time, device and `simulated`). It carries never a level, score or alert; the contract test pins its exact fields. oasdiff reports no breaking change.
- **Kiosk:** `VitalsPanel` has a "Measure vitals" button, tiles for pulse, SpO2 and temperature (blood pressure when present), a "Simulated device" badge, and the exact text "Pulse oximeter: warns about known skin-tone limits" under SpO2. It polls `GET /v1/sessions/{id}/status` every second. `/vitals` hosts the panel (`?session=` and `?scenario=` for dev and E2E; `?scenario=` starts the measurement at once); M1-13 moves it into the check-in flow.
- **E2E against the mock API:** Prism serves the static contract examples, which cannot follow a scenario. The Playwright test therefore routes the dev and status calls and replays `tests/e2e/fixtures/vitals-hypoxic.json`, the simulator's own seed-42 output, which a Python test keeps equal to the simulator.
- **Problem details everywhere:** the error handler is now registered for Starlette's `HTTPException`, so unknown routes (404) and methods (405) are also `application/problem+json`. The dev route's 404 needed this.
- **LOINC:** `devices.base.LOINC` follows `packages/contracts/fhir/mapping.yaml` (a test compares them): SpO2 is 2708-6, with 59408-5 as the second coding in the mapping; the issue's 59408-5 alone would not match the FHIR vital-signs profile.

## Consequences

- M1-12 implements `POST /v1/sessions/{id}/vitals` (readings become `DEVICE_MEASURED` facts, then the rules run) and `GET /v1/sessions/{id}/status` with `vitals`. The dev endpoint then sends into that handler instead of the in-memory store.
- M1-11 can replace the 1-second polling with live events. M1-13 places `VitalsPanel` in the kiosk flow; M1-19's E2E journeys drive the scenarios by query parameter or `VITALS_SCENARIO`.
