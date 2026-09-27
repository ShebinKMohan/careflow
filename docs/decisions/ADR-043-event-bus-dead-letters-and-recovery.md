# ADR-043: Dead-letter poison events, never acknowledge a failed write-back, survive Redis outages

Status: accepted, 27 September 2026 (SHE-303 [M1-12]; the dead-letter part began in the
`fix-events-dead-letter` worktree, SHE-283 [M4-03] builds on it for outgoing webhooks)

## Context

The event bus (M1-11) delivers `careflow:events` to consumer groups at least once: a handler
that raises is not acknowledged, and `XAUTOCLAIM` retries it every 30 s. Four failures showed
up together:

1. **Poison entries were retried forever.** API tests published `queue.status` frames to the
   dev stream for clinics that exist only in `careflow_test`. Every delivery broke the
   `processed_events` foreign key, so the dev API's `fhir-writeback` group retried them every
   30 s, forever, logging a traceback each time.
2. **A failed FHIR write-back was acknowledged.** The `fhir-writeback` consumer logged the
   `failed` status the handler returned and returned normally, so the bus recorded the event as
   processed and acknowledged it. The write was never tried again: a triage confirmed while the
   EHR was down never reached it, and nothing showed that.
3. **A Redis transport error ended the consumer.** `EventBus.run` did not catch errors from
   `XREADGROUP`, `XAUTOCLAIM` or `XGROUP CREATE`. One dropped connection or Valkey restart ended
   the background task, silently, until the API restarted.
4. **Tests shared the dev stream**, which is how (1) started.

## Decision

- **Failure categories.** A handler reports failure by raising, never by returning.
  `PermanentFailure` (the entry can never be processed) joins the entry's own faults
  (`MalformedEntry`, `IntegrityError`, `DataError`) as poison: at its fifth delivery
  (`MAX_DELIVERIES`) the entry is copied to `careflow:events:dead` with its error (the driver's
  message, never the SQL statement or its parameters), its source id, group, consumer and
  delivery count, then acknowledged. A crash between the copy and the acknowledgement leaves it
  pending, so it may be dead-lettered twice but is never lost. `TransientFailure` and every
  other error (Postgres or Redis down, a timeout) are retried for as long as they last;
  `TransientFailure` is logged as a warning without a traceback.
- **Write-back.** `WritebackOutcome.retryable` is true when the FHIR server was unreachable, timed
  out or answered 429/502/503/504 on every attempt (the client's retry statuses). The consumer
  raises `WritebackUnavailable` (transient) for those, so the event stays pending and is retried
  until the write succeeds, and `WritebackRejected` (permanent) when the server rejected the
  Bundle or none could be built, so it is dead-lettered with the reason. Because the handler
  raises inside the bus transaction, the `processed_events` row is rolled back and only the
  delivery that succeeds records the event. Every write is a conditional create, so a retry
  stores nothing twice.
- **Redis outages.** `EventBus.run` catches `RedisError`, waits (0.5 s, doubling to 30 s, reset
  once it consumes again) and starts over: it re-creates the group if Valkey lost it and
  reclaims the entries left pending. `EventBus.status[group]` says `starting`, `consuming` or
  `reconnecting`, for readiness checks.
- **Test isolation.** API tests never use the dev stream: the `app` fixture gives each test a
  stream of its own (`CAREFLOW_EVENT_STREAM`), deleted afterwards with its dead letters, and
  after-commit broadcasts are drained before it goes.

## Consequences

- A confirmed triage written while the EHR is down reaches it within about 30 s of the EHR
  coming back, with no operator action. A rejected write is visible in the dead-letter stream
  (and, as before, as `failed` in `fhir_writeback_status`), never silently acknowledged.
- The dead-letter stream is capped like the main stream (`MAXLEN`). Nothing replays it yet; an
  operator can read it with `XRANGE careflow:events:dead - +`. Replay and alerting on it belong
  to SHE-283 [M4-03] and SHE-289 [M5-05].
- An entry retried "forever" is still visible: its XPENDING delivery count grows. There is no
  upper bound on a transient failure, by design; an outage of hours means retries every 30 s.
- A group re-created after Valkey lost its data starts at `$`: entries written while Valkey was
  empty and before the group existed would be missed. Valkey persists (`--save 60 1`), and the
  outbox relay re-publishes unpublished rows, so this needs both a data loss and a race.
