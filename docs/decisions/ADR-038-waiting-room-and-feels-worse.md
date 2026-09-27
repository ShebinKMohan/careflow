# ADR-038: The waiting room and "I feel worse"

## Status

Accepted, 27 Sep 2026 ([M2-07]). This is the core build. The pytest cases, the two-context Playwright latency test and the event schema test follow in the test phase.

## Context

Patients can get worse while they wait, so "I feel worse" is a safety feature. The live queue also reduces crowding and worry at the desk. The contract had the companion routes, but the API had none of them. Two further gaps:

- **No re-alert from the rules.** Re-running the rules on a session raises nothing for a rule that already fired: `alert.raised` goes out once per rule id.
- **No banner after confirmation.** The nurse station hides alert banners once a level is confirmed, and a waiting patient's level is always confirmed.

## Decision

### "I feel worse": the order matters

`POST /v1/companion/feels-worse` accepts phone-channel tokens only (`require_phone_session`). It returns 202, the press's flag id and the press count.

1. **Record and emit first.** One transaction stores the press in `feels_worse_reports` and adds `patient.feels_worse` to the outbox:
   - The table (migration 0008) is append-only and RLS-scoped. It holds the answer codes and the optional note (at most 280 characters).
   - A lock per session numbers concurrent presses. Every press is kept and counted; none is rate-limited or merged away.
   - The event carries `press_count` and `danger_answer_codes`, never the note. The two fields are optional in the schema so the change is additive, but they are always sent.
2. **Answers become facts.** They are mapped to PATIENT_STATED TriState facts, citing a turn that records the press in the session language (`careflow_intake/feels_worse.py`):
   - Ticked chest items: `chest_pain.present` / `chest_pain.short_of_breath` = yes.
   - Ticked FAST items: `fast.*` = yes.
   - Unticked items are not a "no".
   - "Drowsy or confused" is a fact only for a child: `child_danger.hard_to_wake` = yes, or unknown for "not sure", which the rules treat as red (ADR-035). For an adult it appears on the nurse's card only; no fact is invented.
3. **The rules run again.** The facts merge into the graph state, the rules run at once, and a new rule id goes out as `alert.raised` (safety rule 1). In the smoke run, a ticked chest pain raised `CHEST_PAIN_ECG_10MIN`.
4. **A FHIR Flag.** One `Flag` per press (`<session>:feels-worse-<n>`, created only once, text from the codes, never the note) is written in the background, when the session has an EHR patient.

A failure after step 1 is logged and still answers 202: the nurse was told. No model is in this path.

### The nurse sees every press

- **The banner.** The worklist item gains `feels_worse`: the count, the latest time and answers, and the latest note. The nurse station shows it as a separate, silent banner with a visual pulse (no sound or vibration, as with every alert). This is independent of the level-confirmation logic that hides rule banners.
- **Repeats.** A repeated press raises the count on the same banner.
- **Refresh.** The live worklist refetches on `patient.*` frames, so the banner appears as soon as the event is relayed.

### The live waiting room

- **Status.** `GET /v1/companion/status` (phone only) returns `state`, `position` (1 is next; null when not waiting), `wait_estimate_minutes`, `token`, `room` and `patient_type`. It never carries a level.
- **Broadcasts.** Every queue change sends `queue.status` (the same shape, without `patient_type`) to each waiting or called session of the department:
  - after a call, arrived, no-show or close (routes);
  - after a token is issued (the nurse's decision);
  - after the grace window expires.
- **Frame type.** It is WebSocket-only: published straight to the stream, never a webhook, and allowed on the session channel.
- **Timing.** The broadcast must see the committed queue. The request's database session commits after the route and after FastAPI's background tasks, so the routes schedule it from the session's `after_commit` event (`aftercommit.py`), which never fires on a rollback. The smoke run caught the first version sending a stale position.
- **The phone.** It shows the status card (token, room when called, position, wait, "updated hh:mm"). It listens on the session channel with a one-time ticket, reconnects with backoff and a fresh ticket, and polls every 15 s while the socket is down.

### The phone UI

- **"I feel worse".** A sticky red button opens one screen with the three questions as big buttons, plus an optional note.
  - Success shows "The nurse has been told. Stay where you are."
  - Failure shows, in red, "We couldn't reach the nurse. Tell the reception desk now."
  - A child session gets the child wording for the drowsiness question.
  - The patient is never given advice (safety rule 2).
- **"Ask the clinic".** The panel renders the answer, citation chips (title, doc id), and the `refused_medical` and `dont_know` states. Its backend is M2-09; until then the mock answers, and the real API says questions are unavailable.

## Consequences

- A waiting patient can re-alert the nurse in one screen, in either language. The event goes out before anything else can fail, and every press is counted.
- **Contract changes.** The companion's request and response changes break oasdiff: new required answers, and a nullable position and wait. They are recorded in the ignore file, because only the companion calls these routes and it changed in the same PR. The event fields are additive.
- **On the local stack (smoke run):**
  - the phone status showed position 2 with a 5-minute wait, and a kiosk token got 403;
  - after the nurse called the patient ahead, the phone's `queue.status` frame moved up one;
  - two presses (count 1 and 2) were recorded;
  - the chest-pain press raised `CHEST_PAIN_ECG_10MIN`;
  - the worklist showed the count and the note.
- **Pending for the test phase:**
  - pytest: all "no" still emits with no alert; face/arm/speech raises FAST; "not sure" acts as yes for a child; the event is emitted when the rules fault; 403 for a kiosk or another session's token;
  - Playwright: the nurse card within 2 s, and the queue position updating without a reload and after a killed socket;
  - the no-free-text schema test.
