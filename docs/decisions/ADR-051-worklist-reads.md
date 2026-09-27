# ADR-051: The nurse's worklist reads each visit once, and no screen goes backwards

Status: accepted, 27 September 2026 (SHE-325, a follow-up of M6-01's latency work)

## Context

The nurse's worklist (`GET /v1/nurse/worklist`) reloads on every alert and every "I feel worse",
so its time is part of every deterioration alert's way to the nurse. It read the open visits one
after another, and for each one:
- the LangGraph checkpoint twice (`state()`, then `waiting_for()`);
- the queue token in a database session of its own.

On the e2e stack with 161 open visits, one load took 935 ms (median of 15). The journeys' 2 s
budgets, asserted on the reference machine, failed on cold runs: the chest-pain alert at 2,153 ms
and "I feel worse" at 3,158 ms (docs/FLAKY.md).

Making the load faster exposed two older bugs. With both, the nurse's worklist could show a
closed patient's "Seen (free the room)" again: the "I feel worse" journey failed once on a CI
runner, and once in four runs here.
- **The queue routes committed after sending their response.** They take the request session
  through a `yield` dependency, and FastAPI runs its exit (the commit) after the response by
  default. A client that acted on the 200 at once could read the state from before the commit.
- **The screens applied whatever answer came last.** Reads overlap: a live frame's refetch, the
  queue controls' reload, the poll, a reconnection's snapshot. An older, slower answer replaced a
  newer one on:
  - the worklist;
  - the nurse's session card;
  - the phone's status, which could even replace a newer pushed `queue.status`.

## Decision

- **One checkpoint read per visit.** `IntakeRuntime.view()` returns the state and where the graph
  is paused from a single `aget_state`.
- **Concurrent, with a bound.** The worklist reads the visits' views concurrently, at most 8 at a
  time. The runtime's pool holds 10 connections, which leaves room for the other requests.
  - Measured at 287 to 341 visits, 16 at a time with a pool of 20 was no faster: about 1.8 to
    1.9 ms per visit either way.
  - The remaining cost is decoding the checkpoints in the API's event loop, not the database.
- **One query for every token.** `QueueService.session_tokens()` applies `session_token`'s rule (a
  visit's newest active token, else its newest one) to many visits in one query. `session_token`
  now uses it, so there is one rule.
- **The transaction commits before the response.** `deps.Db` injects the request session with
  `scope="function"`, so a 200 means the change is committed. A test fails if any route takes the
  session the old way.
- **No screen goes backwards.** The worklist, the nurse's card and the phone's status number their
  reads. An answer is shown as soon as it comes, unless a read started after it is already on
  screen; then it is dropped.
  - On the phone, a pushed `queue.status` counts as newer than every read still on its way.
  - Nothing waits for the newest read, so a fast answer is never held back.
- **The output is unchanged.** A test rebuilds the worklist the old way, one visit at a time, and
  compares.

## Consequences

- A worklist load takes 325 ms at 161 open visits (935 ms before), and 547 ms at 287.
- The chest-pain alert stayed under 2 s in every run measured: 574 to 1,118 ms at 161 to 197 open
  visits, and 974 to 1,384 ms at 287.
- "I feel worse" took 1,151 to 1,166 ms at 161 to 197 open visits. At 287 it took 1,701 to
  1,881 ms, and one run of six failed its 2 s budget at 2,265 ms (docs/FLAKY.md). The load still
  grows with open visits.
- **A worklist projection** (one row per visit, kept current by the graph) would make the load a
  single query and stop that growth. M6-01 owns it if the budgets need it.
  - A real clinic closes visits as patients are seen.
  - The e2e test stack keeps every journey's visits open for 24 hours, so it tests far more open
    visits than a clinic would hold.
