# ADR-026: The transactional outbox, the event stream and live WebSockets

## Status

Accepted, 27 Sep 2026 ([M1-11]).

## Context

A sepsis alert that is saved but never delivered is worse than no alert. Every state change has to publish its event reliably:

- to the consumers that act on it (the FHIR write-back, and later webhooks and the audit log);
- live to the nurse station, doctor screen, queue board and patient screens, filtered by clinic and role;
- with alerts reaching a nurse in under a second.

The issue asks for a Postgres outbox, a relay to Redis Streams (Valkey in compose), consumer groups with idempotency, and WebSockets with one-time tickets.

## Decision

### Outbox (`events/outbox.py`, Alembic `0004`)

- **`OutboxPublisher.add(db, event)`** inserts into `outbox_events` in the caller's transaction. A rolled-back change never publishes; a committed one always will.
- **Checked first.** `Event(type, data, timestamp, idempotency_key)` is validated against the contract model of its type: the webhook types, plus the internal `fhir.write.completed`. An unknown type or the wrong data model raises.
- **Idempotency.** The insert uses `ON CONFLICT (clinic_id, idempotency_key) DO NOTHING`. Keys are unique per clinic, not globally, so one clinic's key can never swallow another clinic's event. A missing key gets a fresh UUID.
- **RLS.** `outbox_events` is clinic-scoped with forced RLS, so an event can only be written for the caller's own clinic.
- **Columns.** `seq` (an identity column) keeps insertion order. `created_at` uses `clock_timestamp()`. `schema_version` is the contract version, `0.1.0`, and a test keeps it equal to `openapi.yaml`.

### Relay (`events/relay.py`)

- **Every 100 ms, for each clinic in turn,** it selects unpublished rows `ORDER BY seq LIMIT 100 FOR UPDATE SKIP LOCKED`, `XADD`s them to `careflow:events` (`MAXLEN ~ 100000`), stamps `published_at` and commits.
- **Stream fields:** `id`, `type`, `clinic_id`, `session_id`, `payload` (the envelope as JSON) and `created_at`.
- **Failures.** A crash between `XADD` and the commit re-publishes the rows, so consumers dedupe. If Redis is down, rows stay in the outbox with `attempts` and `last_error`, and go out when it is back.
- **No privileged role.** The issue suggests a separate DB role that sees every clinic's rows. That role would need `BYPASSRLS`, which only a superuser can grant, and the init script runs only on an empty volume. Instead the relay reads each clinic in its own RLS-scoped transaction as `careflow_app`, like the queue's grace task: no role can see every clinic, and isolation holds even in the relay.
  - The cost is one transaction per clinic per tick. That is trivial for the prototype's clinics.
  - The test database, with the many clinics tests leave behind, measured p95 135–178 ms.
  - A deployment with many clinics would add the role to `infra/postgres/init` (see Consequences).
- **Where it runs.** In the API lifespan (`CAREFLOW_OUTBOX_RELAY`), or as its own process with `careflow-api relay`.

### Consumers (`events/bus.py`)

- **`EventBus.subscribe(group, handler)`** creates the consumer group from `$` (BUSYGROUP is ignored), then loops `XREADGROUP ... COUNT 50 BLOCK 1000`.
- **Idempotency.** In one transaction scoped to the event's clinic, it first inserts `processed_events(group, event_id)`. If the row exists, the handler is skipped; otherwise the handler runs. After the commit the entry is acknowledged (`XACK`).
- **Failures.** A handler that fails is not acknowledged. `XAUTOCLAIM` (entries idle 30 s) retries it, at start and while running. Dead-lettering is M4-03.
- **Typed replies.** `events/streams.py` converts redis-py's loose reply types into typed `(id, fields)` pairs.

### Live WebSockets (`events/ws.py`, `events/tickets.py`)

- **Tickets.** `POST /v1/ws-ticket` stores a random ticket (`token_urlsafe(32)`) in Redis for 30 s. The socket redeems it with `GETDEL`, so it works once. A missing, used or expired ticket closes the socket with 4401; the wrong channel or session closes it with 4403.
  - Channel rules: staff roles (nurse, doctor, board, admin) use the staff channel. Kiosk and patient session use the session channel, and a patient session only for its own session.
  - Admin is added to the contract's ticket roles, because the issue gives admin every event.
- **Fan-out.** Each API process runs one `LiveHub`: a plain `XREAD BLOCK` loop from `$`, not a consumer group (a group would split events between processes). It pushes matching events to that process's connections.
- **Filters** (the clinic always matches):

  | Role | Receives |
  |---|---|
  | nurse | `alert.*`, `triage.*`, `token.*`, `intake.*`, plus `patient.feels_worse` and `nurse.flag`, which exist for nurses |
  | doctor | `triage.confirmed`, `intake.completed` |
  | board | `token.called`, `token.no_show` |
  | admin | every webhook type |
  | patient session | only its own `token.called`, `token.no_show` and `intake.completed`. Never an alert, a suggestion or a level (`triage.confirmed` carries the level). |

  Internal events never reach a socket.
- **Frames.** Each frame is `{id, type, timestamp, data}`, and a heartbeat frame follows 20 s of silence. `?last_event_id=` replays the missed entries (`XRANGE`, at most 5 minutes back). The connection is registered before the replay, so nothing falls in between.
- **Delivery log.** `event_delivery_ms` (outbox insert to socket send) is logged per send.
- **Tracing.** `/v1/ws/*` is excluded from tracing: a WebSocket URL carries its ticket, and the connections are long-lived. Before this change the console exporter printed the full URL.

### Wiring

The API's publisher is now `OutboxPublisher`. The queue's `token.called` and `token.no_show` go through the outbox (M1-10's in-memory publisher remains for unit tests). The lifespan starts the grace task, the relay and the hub, each switchable (`CAREFLOW_QUEUE_GRACE_TASK`, `CAREFLOW_OUTBOX_RELAY`, `CAREFLOW_LIVE_EVENTS`), and closes the shared Redis client. `websockets` becomes an API runtime dependency (uvicorn's WebSocket implementation). CI now starts Valkey (the compose image, pinned by digest) and requires it (`CAREFLOW_REQUIRE_REDIS=1`).

## Consequences

- **M1-12** registers the M1-09 write-back handler as consumer group `fhir-writeback`, adapting `WritebackHandler`'s `publish(type, data)` port to `OutboxPublisher.add`. It emits alerts with the idempotency key `{session_id}:{rule_id}`.
- **M4-03** (webhooks, dead-letter) and **M5-01** (audit log) are further consumer groups.
- **M1-15 and M1-17** clients connect with tickets and reconnect with `last_event_id`. The REST API (`GET /v1/nurse/worklist`, session status) stays the fallback when Redis is down.
- **Many clinics** would call for a relay role with a policy over every clinic's outbox rows, created by `infra/postgres/init`, instead of one transaction per clinic per tick.
