# ADR-031: The intake graph behind the API

## Status

Accepted, 27 Sep 2026 ([M1-12]). Core built first by the owner's instruction (27 Sep 2026); the contract and flow tests follow.

## Context

M1-12 joins the kiosk, the nurse station, the rules, the models and FHIR. The LangGraph intake graph (M1-05) runs behind the patient and nurse routes of `openapi.yaml`, and every real port that has landed is wired in. The contract's paths and bodies win over the issue's list.

## Decision

### The runtime (`careflow_api/intake/runtime.py`)

- **The graph and its checkpoints:** `IntakeRuntime` holds the compiled graph over an `AsyncPostgresSaver` on its own psycopg pool, opened in the API lifespan. The thread id is the session id.
- **One step at a time per session:** an in-process `asyncio.Lock` plus `pg_advisory_xact_lock(hashtext(session_id))`, held for the whole graph step. So two requests never advance a thread at once.
- **Checkpoint tables:** `careflow-api setup-checkpoints` creates them as the migrator and grants DML to `careflow_app`; `make migrate` runs it after Alembic. They carry no `clinic_id`, so every read goes through a route that first finds the session under the clinic's RLS.
- **Facts from outside the conversation** (device or nurse vitals) are merged, and the safety rules run at once (safety rule 1). New alerts go into the state and out as `alert.raised`, with the graph's own idempotency key `{session_id}:{rule_id}`.

### Sessions

- **The session row is `patient_session`**, not a new `intake_sessions` table; queue tokens, write-backs and briefings already point at it.
  - Its `device_id` is the kiosk, `fhir_patient_id` the EHR patient and `status` the patient-facing state.
  - Migration 0006 adds `patient_display` (initials only) and `last_activity_at`.
- **`nurse_decisions`** (Alembic 0006, forced RLS, select and insert only): one row per decision, with a `facts_snapshot` of coded values only (numbers, yes/no, codes; never free text).

### The ports (`careflow_api/intake/ports.py`, `wiring.py`)

`build_ports` extends `careflow_intake.wiring.build_real_ports`:

- **identifier:** the check-in's identifier, carried in the initial state.
- **EHR:** the M1-08 reader, with the check-in's lookup reused by the graph (a 120 s cache).
- **urgency:** the M1-04 model from `TRIAGE_MODEL_PATH`; a patient outside its data gets no suggestion. Without a model file it is `NoTriageModel`: never a suggestion, so the nurse sets the level. Never a fake level.
- **queue:** M1-10's `assign_token`; the session becomes `queued`.
- **write-back:** M1-09, synchronously, so the briefing's document can reference what was written.
- **briefing:** M1-16. It is built in a background task, so the decision returns in about 0.3 s instead of waiting for the model summary; a doctor who opens it first gets it built on demand.
- **events:** the transactional outbox (M1-11).

The choice for each port is logged at startup and listed on `GET /v1/admin/status` (`port:*` entries), with the AI tasks' providers and models (`ai:*`), the ruleset and the urgency model version. `GET /v1/admin/providers` lists the providers.

**The `fhir-writeback` consumer group** (M1-09) consumes `triage.confirmed` as the at-least-once safety net:

- it takes the session lock, so it waits for the graph's own write-back;
- it skips a session already written.

HAPI answered a concurrent twin Bundle with 409, which overwrote the successful status; the lock and the skip prevent it.

### The routes

- **`POST /v1/checkin/keypad`** (kiosk):
  - the kiosk must check in for its own clinic, as itself;
  - digits map to the keypad identifier (adapter config) and go to the EHR lookup;
  - unknown digits get a generic 404;
  - it creates the session, starts the graph, and returns the first question and a 30-minute `patient_session` token scoped to the session.
  - **Guessing protection:** only failed attempts count, 5 per 5 minutes per kiosk, and then 429. Counting successful check-ins would throttle a busy kiosk.
  - **Patient display:** initials from the FHIR Patient's name; "Unknown" when there is no EHR patient.
- **`POST /v1/sessions/{id}/turns`** (the session's own token): resumes the patient interrupt.
  - The reply is the next question, or done.
  - 409 when the graph is not waiting for an answer; 422 for an invalid answer.
- **`GET /v1/sessions/{id}/status`:** the state, token, room and the kiosk's latest vitals. Never a level or an alert.
- **`POST /v1/sessions/{id}/vitals`** (kiosk): DEVICE_MEASURED facts, with the rules run at once. The dev vitals simulator feeds the same path.
- **`GET /v1/sessions/{id}/facts`** and **`/triage`** (nurse, doctor).
- **`GET /v1/nurse/worklist`:** today's open sessions, with alerts, the suggested and confirmed levels, open tasks, age, sex, complaint and warnings (`intake_truncated` becomes `intake_stopped_early`).
- **`POST /v1/nurse/sessions/{id}/decision`:** resumes `nurse_review`.
  - 409 when the session is not waiting there; 422 for an invalid decision, and the graph keeps waiting.
  - The nurse decides as themselves: `nurse_user_id` must be the caller.
  - Writes the `nurse_decisions` row and returns the queue token.
- **`POST /v1/nurse/sessions/{id}/vitals`:** NURSE_CONFIRMED facts and the full NEWS2.
- **`POST /v1/nurse/sessions/{id}/tasks/{task_id}/complete`** (idempotent):
  - adds the `tasks.ecg_done_at` fact;
  - completes the written FHIR Task, or the write-back writes it completed;
  - rebuilds the briefing (M1-16).
  - Task ids are `tsk_<required_action>` within the session.
- **`GET /v1/doctor/sessions`:** the confirmed sessions with a token.

## Consequences

- **Tests** (schemathesis contract tests, the httpx flow, concurrency, no model, `-m hapi`) are written after the core system is built. The issue stays In Review until then.
- **M1-19** starts the stack with the runtime; **M2-05** and **M2-06** add the ID-tap, consent and QR routes on the same runtime.
- **The complaint category** (a model-inferred urgency feature) is not yet produced in the live intake. The model reads it as not recorded.
