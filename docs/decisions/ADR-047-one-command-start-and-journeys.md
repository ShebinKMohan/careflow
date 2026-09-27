# ADR-047: One-command start per stack, and journeys on the real stack

Status: accepted, 27 September 2026 (SHE-315 [M1-19]; builds on ADR-046's isolated stacks)

## Context

M1-19 closes the MVP: `make up` must start everything from a clean clone, and three journeys
(chest pain, routine walk-in, sepsis) must pass through the kiosk, the nurse station and the
doctor briefing against the real API, Postgres, Valkey and HAPI, with the write-back checked in
HAPI. Four things stood in the way:

- `make up` and `Procfile.dev` hard-coded the dev ports, so a second checkout (or the checks
  here) could not run beside the owner's dev stack, and `make down` stopped whatever
  `.run/honcho.pid` named.
- `make up` returned when every process answered HTTP, even with the API's database, Valkey or
  FHIR server down.
- `LOCAL_LLM=1` started `mlx_lm.server` itself, but the runbook allows the local model only
  through `scripts/with_llm.sh` (one model at a time, stop only what you started).
- The journeys need deterministic model replies, yet the handoff also asks for a real
  local-model run.

The journeys then found three bugs, fixed here: the real intake ports read the stubs' fixed
clock, so every alert was stamped 09:00; a re-raised alert took each evaluation's time instead
of its first raise, so `raised_at` in the briefing, the FHIR Flag and the audit drifted later
with every turn; and the API waited for the simulated vitals stream on shutdown until honcho
killed it.

## Decision

- **Every stack by name.** `CAREFLOW_STACK` picks the stack (default `careflow`, the dev stack
  on the CONVENTIONS ports) and `CAREFLOW_PORT_OFFSET` its offset (`careflow-test` defaults to
  +10000). `scripts/stack.py` writes the stack's ports and public URLs to
  `.run/stacks/<stack>.env`; `make up` sources it, honcho loads it over `.env` (so the stack's
  ports win and the secrets stay in `.env`), and `Procfile.dev` reads every port from it. Each
  stack has its own pidfile, log and Procfile under `.run/stacks/`. One set of apps per checkout
  (they share `.next`): `make up` refuses a second stack's apps from the same checkout. `make
  down` stops the named stack's processes, then its Docker services if this checkout owns them
  (ADR-046); the volumes stay.
- **Ready means ready.** `scripts/wait_healthy.py` accepts the API only when `/health` reports
  its database, Valkey and FHIR server as "ok", then prints "CareFlow is up".
- **The local model through with_llm.sh.** `LOCAL_LLM=1` adds `llm: scripts/with_llm.sh tail -f
  /dev/null` to the stack's Procfile: the sanctioned script serves the pinned Qwen3.5-9B (or
  reuses a server already on 8081), and honcho signals its whole process group on `make down`,
  so the server it started stops with the stack.
- **A bounded API shutdown.** uvicorn gets `--timeout-graceful-shutdown 3`, inside honcho's 5 s:
  a request's background task (the simulated vitals stream) is cancelled and the lifespan
  shutdown runs, instead of a SIGKILL mid-write.
- **Three model modes for the journeys.** `E2E=1` replays every model reply from
  `e2e/replays/providers.cassette.json` (M0-06's `ReplayProvider`, which fails loudly on a miss;
  `make up` refuses to start without the cassette); `E2E_RECORD=1` records it from the local
  model; `E2E_LIVE=1` uses the local model with nothing replayed. All three use
  `e2e/config/intake.yaml` (fixed question wording, no LLM briefing summary), so a recording
  replays turn for turn. CI and the three-run check use replay; the evidence keeps a live run.
- **What the journeys assert.** The root `e2e/` package (Playwright, one worker, no retries)
  signs in through the real login and device routes and drives the real apps: the ECG alert on
  this session's worklist row before any level, the countdown stopped by "ECG done", no token
  before the nurse decides, a decision from the kiosk device or the patient refused (403), a
  second decision refused (409), one token shown alike on the kiosk, the nurse's card, the API
  and the public board, the room on the kiosk once the nurse calls the patient in, the patient's
  own words marked in the doctor's source popover, the full NEWS2 after the nurse's vitals, and
  in HAPI one Encounter with the nurse's level, one Flag, one Task (completed, with its
  deadline), one QuestionnaireResponse, one document Bundle and the vitals Observations (one
  final NEWS2 for sepsis; no Flag for the routine visit). Locators are scoped to the session:
  a test stack keeps earlier runs' patients. The staff tokens from the login route are reused
  between runs from `.run/e2e/` (git-ignored, owner-only), so back-to-back runs stay within the
  API's 5 logins a minute; the limit itself is unchanged.
- **Latency is measured, and the journey's bound is functional.** Each journey records how long
  every turn took and, for chest pain, when the alert was raised and when it appeared on the
  nurse's worklist. The chest-pain journey keeps the issue's bound, the alert on the worklist
  within 2 s of the turn's answer, as the check that alerts are pushed live rather than found on
  a later poll. The speed target itself (alert p95 <= 1000 ms, CONVENTIONS) is measured and
  published in the evidence and never gated.
- **The first raise is kept.** `merge_alerts_by_rule_id` keeps an alert's first `raised_at` and
  takes everything else from the new evaluation (evidence, partial, display text), matching
  `alert.raised`, which goes out once per rule id. The real intake ports default to the real
  UTC clock.
- **CI.** An `e2e` job in `ci.yml` runs `make up` headless (replay, `VOICE=0`,
  `SEED_SYNTHEA=0`, Playwright's Chromium) and `make e2e`, uploads the traces and the stack log
  on failure, and runs only on pushes to `main`, on PRs labelled `e2e` and on demand, to spare
  the free minutes.

## Consequences

- Two stacks run side by side without touching each other; the checks here never stop the
  owner's dev stack. A third stack for a fresh-clone check fits in the 6 GiB Docker budget.
- A prompt or question change invalidates the cassette, and replay fails loudly until it is
  re-recorded with `E2E_RECORD=1 LOCAL_LLM=1` (Apple silicon).
- The journeys leave their sessions on the stack and close the queue tokens they call. Run them
  on a test stack: calling a patient in closes any patient called before it.
- The apps run as Next.js dev servers, so the first visit to a page compiles it; the journeys
  allow for that, and a production build for the kiosk is left to M6.
- The local model is slow on the first turn after it starts (about 18 s on this Mac), then 3.5 to
  10.6 s per model-backed turn; the evidence reports it and M6-01 owns the speed work.
