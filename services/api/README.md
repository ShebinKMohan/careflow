# careflow-api

CareFlow's FastAPI service on :8000 (`make api`). It implements `packages/contracts/openapi.yaml` (the contract; FastAPI's own `/openapi.json` is disabled).

```bash
make up && make migrate && make seed-dev   # Postgres/Valkey/HAPI, schema, dev clinics
make api                                   # uvicorn careflow_api.main:create_app --factory --port 8000
curl -s localhost:8000/health              # {"status":"ok","version":"0.1.0","db":"ok","redis":"ok","fhir":"ok"}
```

- **Database**: SQLAlchemy 2 async with psycopg 3. The app connects as `careflow_app` (never an owner or superuser); Alembic (`services/api/alembic/`) and `seed-dev` run as `careflow_migrator`.
- **Row-level security**: every clinic-scoped table forces RLS with the `clinic_isolation` policy. `get_db()` yields one transaction per request with `app.clinic_id` set from the token; no setting means no rows (ADR-014). New migrations must grant, enable and force RLS, and add the policy (`tests/test_rls.py` checks every table).
- **Auth**: `POST /v1/auth/login` (5/min/IP), `POST /v1/auth/device-token` (kiosk, board, integration), `GET /v1/auth/me`. Dependencies for later routers: `get_principal()`, `require_roles(*roles)`, `get_db()`.
- **Telemetry**: OpenTelemetry spans for every route except `/health`, console exporter by default, OTLP when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. No names, dates of birth or free text in spans or logs.
- **Simulated vitals device** (`devices/`, M1-03, ADR-018): there is no real hardware, and every reading is labelled simulated. `SimulatedSerialDevice` plays `devices/scenarios/*.yaml` (`normal`, `septic`, `hypoxic`, `tachycardic_child`) as serial frames with seeded noise. `careflow-vitals-sim --session <id> --scenario septic --api http://localhost:8000 --token <kiosk_device token>` posts one reading per frame to `/v1/sessions/{id}/vitals`. Blood pressure is sent only with a cuff scenario and `SIM_BP_CUFF=1`. With `CAREFLOW_ENV=dev`, `POST /v1/dev/sessions/{id}/vitals-sim` runs the simulator for a session; otherwise the route does not exist (404).
- **Queue and tokens** (`queue/`, M1-10, ADR-025): after the nurse confirms a level, `QueueService.assign_token` issues a token (`A-042`) in the department `config/rooms.yaml` routes the level to. Nurses call the next token (level first, then time in the queue; a room takes only the levels it accepts), mark it arrived, missed or closed. A missed call is re-queued once, then it is a no-show (the grace window runs every 10 s in the API process). The board (`GET /v1/board/{department}`, role `board`) shows only token code, room and status. `python -m careflow_api.queue.sim` runs the 200 arrivals/hour simulation and writes `eval/reports/queue/`.
- **Tests** build a throwaway `careflow_test` database (superuser only to create it). Without Postgres they skip, unless `CAREFLOW_REQUIRE_DB=1` (CI) makes them fail.

Research prototype. Synthetic data only. Not for clinical use.
