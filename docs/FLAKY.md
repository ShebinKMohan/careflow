# Flaky and machine-dependent tests

This file lists tests whose outcome depends on the machine or on timing, and how each one is handled. The runbook forbids weakening an assertion, adding retries or skipping a safety test to get a green run. So each entry states what still runs in CI.

## `services/triage/tests/test_train_inference.py::test_predict_returns_three_reasons_fast[hgb]` (M1-04)

- **What happened.** The first CI run of PR #12 (GitHub run 36272705313) failed on one HGB `predict()` call of 21.4 ms. The budget is 20 ms. On the reference machine (M3 Pro), the slowest call was 5.2 ms at the time, and it is 4.1 ms after the fix below.
- **Cause.** A shared GitHub runner is slower and noisier than the reference machine. Per call, the model walks 1,000 trees and computes TreeSHAP.
- **Handling.**
  1. `predict()` was made faster first: the one-thread OpenMP limit now uses a controller built once per model, which saves about 1 ms per call.
  2. The owner decisions (CONVENTIONS) say speed targets are measured and published, never CI gates. So the 20 ms assertion runs on the reference machine (any run without `CI=true`).
  3. On a CI runner, the test issues a warning with the p50 and slowest call when a call exceeds the budget, instead of failing.
- **Still asserted in CI.** Every call must return a contract-valid `UrgencySuggestion` with 3 reasons and a `model_version`.

## `apps/staff/tests/e2e/nurse.spec.ts` › "ECG done sends the task-complete request once and stops the countdown" (M1-15)

- **What happened.** During SHE-313 [M1-17] on 27 September 2026, one full `pnpm --filter staff
  test:e2e` run out of four failed this test at `expect(ecg-status).toContainText("07:00")`
  (the 5 s expect timeout, 5.4 s in all). The other three full runs and three runs of the test
  alone passed. The M1-17 changes do not touch the nurse screens.
- **Suspected cause.** The test installs the fake clock at the alert time, loads the card, then
  pauses the clock at alert + 3 min and expects the countdown to read 07:00. If the card's first
  render (or its triage fetch) lands on either side of `pauseAt` differently from run to run, the
  countdown may be computed before the pause and not recomputed within the timeout.
- **Handling.** Not retried, not weakened. Reproduce and fix with SHE-302 [M2-07], which changes
  the nurse card's refresh (the card must re-read alerts live). Until then the test runs as it
  is in CI; a red run of it is investigated, not re-run until green.
- **Resolved (SHE-302, 27 September 2026).** The cause was confirmed in the code: `useNow` read the
  clock once at the first render and then only on its 1 s interval, which is registered in an
  effect after the first paint; when the test paused the clock between the two, the countdown
  stayed at the mount time ("10:00"). `useNow` now reads the clock again as the effect runs. The
  card's new live reload (M2-07) then exposed a second race in the test's stub: a reload scheduled
  before the pause could land after "ECG done" and bring back the stub's open task. The stubs now
  keep state like the API (a completed task, a decision and saved vitals stay in later reads).
  With both, the whole staff suite passed 5 runs out of 5 (18 passed each); the assertion is
  unchanged.

## `e2e/journeys/chest-pain.spec.ts` alert-latency budget (M1-19)

- **What it measures.** The time from the kiosk receiving the answer to the turn that raised
  `CHEST_PAIN_ECG_10MIN` to the chip rendering on the nurse's open worklist; the issue's bound is
  2 s. On the reference machine it was 689 to 1,215 ms in replay and 795 to 1,380 ms with the
  local model (docs/evidence/M1-19.md).
- **Why it is machine-dependent.** It is a speed budget, and a shared GitHub runner is slower and
  noisier than the reference machine; speed targets are never CI gates (CONVENTIONS).
- **Handling.** Asserted (<= 2,000 ms) on any run without `CI=true`; on a CI runner the journey
  adds a `warning` annotation with the measured time when it is over, instead of failing.
- **Still asserted in CI.** The chip appears on this session's row of the already open worklist
  (live, no reload) before any level exists, and every other check of the journey.

## `services/api/tests/test_events_ws.py::test_heartbeats_and_replay_after_a_reconnect` (M1-11)

- **What happened.** During SHE-300 [M2-06] on 27 September 2026, one full `make check` failed
  this test at its first `next_event` (no event within 5 s of publishing one 0.3 s after the
  nurse's socket connected). The machine was running three stacks' apps and the local model at
  the time. Alone, the test file passed three runs out of three (4 passed each, about 16 s); the
  M2-06 changes do not touch the event path.
- **Suspected cause.** The test gives the server 0.3 s to register the new connection before it
  publishes; a fresh connection (no `last_event_id`) only receives events dispatched after it is
  registered, so under heavy load the event can land before the registration.
- **Handling.** Not retried, not weakened. SHE-302 [M2-07] makes clients reload an authoritative
  snapshot on (re)connect instead of relying on events that arrive in the first moments; revisit
  this test's 0.3 s window there. A red run is investigated, not re-run until green.
- **Since SHE-302.** The nurse's session card and the phone's waiting room now reload their
  snapshot whenever their socket (re)opens, so a client never depends on catching an event in the
  first moments of a connection. The server-side test and its 0.3 s window are unchanged; it has
  not failed again in the runs recorded in docs/evidence/M2-07.md.
