# ADR-029: The nurse station

## Status

Accepted, 27 Sep 2026 ([M1-15]).

## Context

Safety rule 3: the model suggests, the nurse decides. The nurse station must show danger first (safety rule 1), explain the suggestion without nudging the nurse into blind acceptance, and make every decision a deliberate act.

The issue asks for:

- a live worklist over `/v1/ws/staff`;
- silent alert banners with evidence and a 10-minute ECG countdown;
- a triage card with confirm and override;
- a form that completes NEWS2.

The contract (M0-03) had gaps: no age, sex or complaint on a worklist row, no NEWS2 result, no warnings, and no task on the triage view.

## Decision

### Contract additions (all optional; oasdiff: no breaking change)

- **`News2Score`** mirrors the rules engine's `News2Result` exactly: `total`, `subscores` (`News2Subscores`, one 0–3 value per measured parameter), `missing` (`FactKey`s), `partial`, `any_single_3` and `risk_band` (low / low-medium / medium / high).
- **`SessionWarning`** is one of `extraction_incomplete`, `ehr_unavailable` or `intake_stopped_early`.
- **`WorklistItem`** gains `age_years`, `sex` (administrative gender), `complaint` (at most 80 characters, staff screens only) and `warnings`.
- **`TriageView`** gains `news2` (null when NEWS2 does not apply or there are no vitals), `open_tasks` and `warnings`. `POST /v1/nurse/sessions/{id}/vitals` already returns a `TriageView`, so the form shows the recomputed NEWS2 from it.
- **Examples** use the rules engine's own scores for the sepsis persona: partial 6 (SpO2 93, HR 125, 39.4 °C) and, after the nurse's vitals, 15 (high).
- **Alert templates for TypeScript.** `alert_templates.yaml` is also generated into `ts/alertTemplates.ts` (`@careflow/contracts/alert-templates`), so a UI renders the same fixed line from `rule_id` when an alert has no `display_text`.
- M1-12 fills the new fields.

### Decisions follow the contract

- **The body.** `POST /v1/nurse/sessions/{id}/decision` takes the contract's full `NurseDecision`, not the issue's `{action, level, reason}` (the contract wins):
  - **Confirm** sends `CONFIRM` with the suggested level.
  - **Change level** sends `OVERRIDE` with an `override_reason_code`. Free text of 5 or more characters counts as a reason and is sent as code `other` plus `override_note`.
  - **No suggestion.** The nurse must choose a level. It is sent as `OVERRIDE` with `suggestion_id: null` and the pre-filled note "no model suggestion".
- **The other fields:**
  - `decision_id` is generated on the page;
  - `nurse_user_id` comes from `GET /v1/auth/me`;
  - `acknowledged_alert_ids` lists the alerts on screen.
- **No early final level.** The token and room appear only after the API accepts the decision; nothing shows a level as final before that.

### Live updates (`lib/liveEvents.ts`, `lib/useLiveWorklist.ts`)

- **Connecting:** a one-time ticket from `POST /v1/ws-ticket`, then `/v1/ws/staff?ticket=…`. Heartbeats are ignored, and each frame's stream id is kept.
- **Events:**
  - an `alert.*`, `triage.*`, `token.*` or `intake.*` event refetches the worklist (debounced 150 ms);
  - there is no single-item route, so the whole small list is re-read;
  - an `alert.raised` also shows its banner at once and loads that session's facts for the evidence.
- **Drops:** a dropped socket, or no ticket, shows "Live updates paused. Checking every 5 seconds.". The page then polls `GET /v1/nurse/worklist` every 5 s and reconnects with backoff (1 s, doubling to at most 30 s), passing `last_event_id`.
- **Sort order (`lib/sortWorklist.ts`):**
  1. an active critical alert, then an active high alert;
  2. the level, 1 first: the confirmed level once decided, else the suggestion, with no suggestion counted as 2;
  3. then arrival time.

  A confirmed session's alerts no longer count.

### Screens

- **Alert banners:**
  - **Silent.** Visual only: a labelled region, not a live announcement, and no sound.
  - **Colour is never the only signal.** Critical banners are filled red and high ones outlined, and the severity and rule name are also written out.
  - **Content:**
    - the fixed alert line;
    - each evidence fact with a readable label and unit (`°C`, `mmHg`) and a provenance chip ("Patient said", "Device", "EHR"…);
    - the time raised;
    - a "Partial NEWS2" badge when partial.
  - **The ECG countdown** sits on a light panel, so an urgent red countdown is never red-on-red:
    - `mm:ss`, red under 2 minutes, then "Overdue +mm:ss" (`h:mm:ss` beyond an hour);
    - "ECG done" completes the task once (disabled while the request is out, with a guard in the handler) and then shows "ECG done hh:mm".
  - **Lifetime:** banners stay until the triage is confirmed.
- **Triage card:**
  - **Warnings come first:** those from the triage view, plus "Extraction incomplete" when the facts say so.
  - **The suggestion** is in a dashed box headed "Suggestion only. You decide.". It shows the scale label from config (`display_label`, from M1-04's `triage_scales.yaml`), confidence, the top 3 reasons and the model version.
  - **Confirm level N** is never auto-focused, and Enter is blocked on every decision button (a click, a tap or Space still works).
  - **Facts** are grouped, each with its provenance.
- **NEWS2 form:**
  - **Inputs:** respiratory rate, systolic BP (pre-filled from a device reading and marked "From device, confirm"), ACVPU buttons and oxygen yes/no.
  - **What is sent:** only the values entered.
  - **What is shown:** the total, the band and each subscore; "Partial: n of 7 missing" until all seven are present.
- **Layout.** Staff screens are desktop screens: the main column grows to 72 rem, so the seven-column worklist fits. The sign-in form stays 36 rem wide and the triage page 56 rem.

### Tests

- **Vitest:** sorting, the countdown, decision bodies and reasons, fact indexing, labels and units, alert text, and the live channel (ticket, frames, heartbeats, backoff, `last_event_id`, no ticket, stop).
- **Playwright:** HTTP is stubbed with `page.route()`, the staff socket with `page.routeWebSocket()`, and time with `page.clock`. Every acceptance criterion is covered, plus the no-suggestion path.
- **Fixtures are checked against the contract.** A contracts test validates every app fixture (`apps/*/tests/fixtures/api/*.json`, kiosk and staff) against `openapi.yaml`, and fails if a fixture file is not listed.

## Consequences

- **M1-12** returns the new worklist and triage fields, and `open_tasks` with the ECG task.
- **M1-17** can reuse `AlertBanner` and `ProvenanceChip` for the doctor screen.
- **M2-01** (Arabic): the strings exist (machine-written). Fact labels are derived from English field names.
- **M5-03** reads the decisions this screen writes.
- **The intake transcript** is not shown: no route exposes it (the issue's "transcript collapsible" is left out; see the evidence).
