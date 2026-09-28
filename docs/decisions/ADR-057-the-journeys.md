# ADR-057: The journeys, on the real stack

## Status

Accepted, 28 Sep 2026 ([M2-12]). Evidence: `docs/evidence/M2-12.md`.

## Context

M1-19 left three journeys; later issues added the child path, the QR hand-over, "I feel worse", the Arabic visit and the consent paths, each with its own checks. M2-12 asks for:
- seven patient journeys and three check-in paths;
- a shared `assertJourney`;
- `@ar` variants;
- a CI job with three consecutive green runs;
- a nightly run against the local model.

The Claude Code handoff adds essential failure checks: model or EHR unavailable, repeated requests, refresh, reconnect, expired credentials, isolation, consent refusal and no stored media.

## Decision

### One set of checks, in one order

`e2e/lib/journey.ts` runs the issue's checks for every journey:

| Step | What it checks |
| --- | --- |
| (a) `assertAlerts` | The expected rule ids, and no others, reach the nurse's open worklist live. The timing is measured from the answer that raised the alert: the last one sent before `raised_at`. A device's reading (the kiosk's simulated device runs server-side) is timed from `raised_at` itself. |
| (b) `assertDecision` | The nurse confirms, sets or overrides with a coded reason on the card. The patient's and the kiosk's tokens get 403, and a second decision 409. |
| (c) `assertTokenAndRoom` | The token waits on the board once. When called, it is on the board with its room. The patient's screen shows the token and room while called: the phone shows the room only then. Board entries carry exactly a token, a room and a state, never the name the nurse sees. |
| (d) `assertBriefing` | Every briefing item has one of the five provenances and a source that checked out. |
| (e) `assertWriteBack` | HAPI holds the session's Encounter once, its QuestionnaireResponse, its Observations (not for a child, who skips the sensors), the briefing's document starting with its Composition, and a Flag exactly when an alert was raised. |

- **The 2 s bound** is asserted on the reference machine and reported on a CI runner (CONVENTIONS: speed targets are never CI gates).
- **Test hooks.** The worklist's alert chips and the card's alert banners carry `data-rule-id`, so a journey finds a rule's alert by its id instead of by translated text.

### A 5-second grace window, for the E2E stack only

- `CAREFLOW_ROOMS_CONFIG` names the rooms file. The E2E stack (`E2E`, `E2E_LIVE` and `E2E_RECORD` in `scripts/up.sh`) loads `e2e/config/rooms.yaml`, a copy with `grace_seconds: 5`.
- A test keeps the copy equal to `config/rooms.yaml` apart from that line, and the shipped 120 s.
- The no-show journey leaves its patient called: re-queued once behind the next patient, a no-show on the second miss, off the board, its room free.
- Every other journey's called patient walks in at once ("arrived", as the nurse's queue controls record it), so the short window never re-queues them. Each journey then closes its token (seen).
- A Playwright global setup frees any room an earlier or failed run left held on the long-lived test stack.

### Recording without re-recording

- `CAREFLOW_PROVIDER_MODE=add` (`E2E_RECORD=add`) answers what the cassette holds and records only the prompts it lacks.
- Before, `record` re-called the model for every prompt and overwrote the reply, so adding a journey silently re-recorded the others'.
- New recordings go in their own commits.

### The journeys, by the issue's names

| Journey | Covers |
| --- | --- |
| `chest-pain` (en, `@ar`) | |
| `stroke` | new, with clinic isolation |
| `child-drowsy` (en, `@ar`) | |
| `sepsis` | |
| `routine` (en, `@ar`) | |
| `qr-phone` | the hand-over, "I feel worse" and the reconnect |
| `no-show` | new |
| `checkin-keypad` | new |
| `checkin-id-tap` | new |
| `checkin-qr` | the M2-06 hand-over checks |
| `ehr-outage` | HAPI paused mid-visit (`docker pause`). The nurse keeps the alert and decides, the admin status says HAPI is down, and the failed write-back lands once HAPI is back. |
| `consent`, `consent-voice` | M2-05 |
| `arabic` | M2-01 |

The voice journeys need a voice service; without one they are skipped with that reason.

### CI and the nightly run

- **The CI `e2e` job.** It runs the whole suite on pushes to `main`, on PRs labelled `e2e` and on `workflow_dispatch`.
- **Repeat runs.** `journeys_only: true` skips the other jobs, so the three repeat runs spend Actions minutes on the journeys alone.
- **Failed journeys.** Their traces and videos are uploaded.
- **The nightly run.** `scripts/nightly-e2e.sh` starts its own stack against the live local model (`E2E_LIVE=1 LOCAL_LLM=1`), runs every journey and writes `eval/reports/e2e_nightly.json`. The report records each spec's result, duration and provider, the served model as `org/model@revision`, and the measured timings.
- **The LaunchAgent.** `infra/launchd/com.careflow.nightly-e2e.plist` (02:00) and `make install-nightly` install it. Loading it is the owner's `launchctl load`, never an agent's.

## Consequences

- Every journey proves the same five things, in the same order, on the real API, Postgres, Valkey, HAPI and apps.
- **Where the failure checks live.** The ones a browser cannot drive stay in the service suites (named in the evidence):
  - the extraction model failing;
  - EHR reads timing out;
  - token expiry and renewal;
  - no files written by a voice session.
- **The journeys share one stack** and one worker. The global setup and each journey's "seen" keep the rooms free between runs.
