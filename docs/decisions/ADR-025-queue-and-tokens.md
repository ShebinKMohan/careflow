# ADR-025: The queue and token service

## Status

Accepted, 27 Sep 2026 ([M1-10]).

## Context

After the nurse confirms a level (safety rule 3: nothing else queues), the patient needs a token such as `A-042`. Rooms then call tokens in a safe order, and a public board shows who is called without naming anyone. The issue also asks for a simulation proving that levels 1–2 are never starved at 200 arrivals per hour.

The contract (M0-03) already has:

- the queue routes;
- `QueueToken`, `BoardEntry` and `CallNextResult`;
- the statuses `waiting | called | arrived | no_show | closed`;
- token codes of the form `^[A-Z]-[0-9]{3}$`.

## Decision

### The rules: `QueueCore` (pure, `queue/core.py`)

- **Order.** Tokens are ordered by the nurse-confirmed level, then `enqueued_at`, then token id.
  - `enqueued_at` starts at the patient's arrival (the session start). At the same level, whoever arrived first goes first, even if triaged later.
  - A re-queue sets it to now, which is the back of the level.
- **Calling.**
  - `call_next(room)` takes the first waiting token whose level the room accepts.
  - `call_next_free()` fills the first free room in config order. Resus bays are listed first, so an urgent patient takes one.
  - A room is busy while a `called` or `arrived` token holds it. `close` (seen) and a no-show free it.
- **Grace window** (`grace_seconds`, 120). A called token not marked arrived in time is re-queued once (`recall_count = 1`); the second miss makes it `no_show`. The nurse's "no-show" button applies the same rule immediately (`miss`).
- **Time.** It comes only from the injected clock.
- **Statuses.** The contract's statuses are used. The issue's `in_room` is `arrived`, and `done` is `closed`.

### Rooms: `config/rooms.yaml`

- **Contents:**
  - the grace window;
  - the mean service minutes per level;
  - `routing` (level to department: 1–3 Emergency `A`, 4–5 General `G`);
  - departments, each with a prefix and its rooms in calling order: id, display name of at most 32 characters (the contract's limit), and `accepts_levels`.
- **Validation at API start.** Department ids and prefixes are unique, room ids are unique, every level is routed, and every routed level has a room in its department that accepts it. A bad file stops the API instead of stranding a patient.

### Persistence (`queue/service.py`, Alembic `0003`)

- **Tables.** `queue_tokens` and `token_counters`, clinic-scoped with forced RLS and the `clinic_isolation` policy. The app role cannot delete from them.
- **Partial unique indexes:** one active token per session, and an active code is unique in its clinic and department.
- **Serialising changes.** Every change to a department's queue takes `pg_advisory_xact_lock` for that clinic and department. It then loads the department's active tokens into `QueueCore`, applies the rule and writes the changed tokens back.
- **Numbering.** Codes are `<prefix>-<NNN>`, per clinic, department and local day (the clinic's time zone). The counter row is locked with `SELECT ... FOR UPDATE`, as the issue asks. After 999 the number wraps to 1 and skips codes still held by active tokens: 200 arrivals per hour would pass 999 in a day, and the contract allows 3 digits.
- **`assign_token` is idempotent per session.** A session with an active token gets that token back. Re-queuing at a new level (re-triage) is a later decision (M2-07).

### Events

- **`token.called`** is published on every call.
- **`token.no_show`** is published on a second miss. It is a new webhook type in the contract: data schema, `EventType`, webhook entry, README and `websocket.md` (the board gets it too).
- **Same transaction.** Both go through `EventPublisher.add(db, event)` in the transaction of the change. `MemoryEventPublisher` stands in until M1-11's outbox, and it already checks each payload against its contract model.

### Routes (`queue/router.py`)

| Route | Role | Notes |
|---|---|---|
| `POST /v1/queue/{department}/call-next` | nurse | Optional `?room=`. 404 for an unknown department or room, 409 for a busy room. |
| `POST /v1/queue/tokens/{id}/arrived` | nurse | 404 unknown, 409 wrong state |
| `POST /v1/queue/tokens/{id}/no-show` | nurse | 404 unknown, 409 wrong state |
| `POST /v1/queue/tokens/{id}/close` | nurse | New in the contract: without it no room would ever be free again. |
| `GET /v1/board/{department}` | board | `token_code`, `room` and `status` only. Called tokens come first (latest call first), then waiting tokens in queue order. |
| `GET /v1/sessions/{id}/token` | patient_session (its own session only), nurse | |

The API lifespan runs `expire_grace` every 10 s for every clinic, one RLS-scoped transaction per clinic. `CAREFLOW_QUEUE_GRACE_TASK=0` turns it off.

### Wait estimate (`queue/wait.py`)

It takes the mean service minutes of the waiting tokens ahead, divides by the number of rooms that accept the level, and rounds up to 5 minutes. It is labelled an estimate and comes back in `TokenAssignment.wait_estimate_min`. Room open or closed state is not modelled: every configured room counts as open.

### Simulation (`queue/sim.py`)

- **Method.** A `heapq` event loop with `random.Random(42)` over `QueueCore` itself: 8 h of Poisson arrivals at 200/h with the issue's level mix and exponential service means. There are 66 rooms, the first 2 levels 1–2 only, and 5% of calls are no-shows. Patients who arrive within the horizon are followed until they are seen.
- **Results** (`eval/reports/queue/sim_20260927.md`):

  | Measure | Result |
  |---|---|
  | Utilisation | 0.814 |
  | p95 wait, level 1 / 2 / 3 / 4 / 5 | 2.0 / 0.4 / 1.1 / 2.7 / 10.0 min |
  | Level 1–2 tokens left waiting with a room free | 0 |
  | Mean waiting count, last hour vs run | 0.00 vs 0.79 |
  | No-shows | 3, each with exactly one recall |

## Consequences

- **M1-11** replaces `MemoryEventPublisher` with the outbox; the token events reach the staff and board sockets.
- **M1-12** adapts the intake's `QueueService` port. `assign(session_id, clinic_id, level)` calls `assign_token` in a clinic transaction and returns `Assignment(token=token_code, room=room_hint)`. The kiosk shows the token and the estimate.
- **M1-17** builds the board and queue admin UI.
- **M2-07** adds re-queue on re-triage.
- **M5-04** adds forecasting.
- **Levels 4–5 wait longest under strict priority.** The report shows their p95; aging is a later decision.
