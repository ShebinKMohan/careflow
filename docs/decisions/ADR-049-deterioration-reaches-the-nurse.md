# ADR-049: A deterioration after the decision reaches the nurse, live

Status: accepted, 27 September 2026 (SHE-302 [M2-07]; builds on ADR-038 and ADR-048)

## Context

ADR-038 built "I feel worse" and the waiting room; its verification was deferred. The review and
the real-stack journeys found that a patient getting worse after the nurse's decision could be
missed:

- **The nurse's session card** loaded once, on mount, and hid every alert banner once the level
  was confirmed. An alert raised later (FAST signs from "I feel worse") never appeared on an open
  card, and even a reload hid it. A reload after "ECG done" showed the ECG as due again, because
  the triage view listed only open tasks. After a decision without a model suggestion, the
  triage view itself failed (a 500: the null `suggestion_id` was dropped).
- **The worklist** dropped a confirmed patient's alert banners, so a new danger sign in a waiting
  patient showed only as a chip in the table, and the patient did not sort first.
- **The phone** hid "I feel worse" whenever a status poll failed, and a reconnected socket did not
  reload what it missed.
- **A press on an ended visit** (closed or a no-show) was stored and answered "the nurse has been
  told", but the nurse station no longer lists such a visit.
- The EHR Flag of a press was skipped whenever the rules re-run failed.
- The nurse had no queue controls.

## Decision

- **Alerts are never hidden by a decision.** The nurse's card shows every alert of the session;
  one raised after the decision is marked "New since the decision". On the worklist, a confirmed
  patient's banner comes back for an alert raised after the decision (`AlertChip.raised_at`,
  `WorklistItem.decided_at`), and that patient sorts first again.
- **The card is live.** A staff-channel frame about the session reloads the card (debounced 150 ms,
  so a duplicated frame is one reload of the server's state), every (re)connection reloads it (the
  snapshot), and while the channel is paused it is re-read every 5 s. A failed reload keeps the
  last card. The card shows the patient's "I feel worse" presses (`TriageView.feels_worse`).
- **The triage view tells the truth after a reload.** `TriageView.tasks` lists every task, done
  ones included; the decision keeps its (nullable, required) `suggestion_id`.
- **The phone's safety button never depends on the status.** "I feel worse" is always there in
  the waiting room; a failed poll keeps the last good status, marked stale; every socket
  (re)open reloads the status. If the press cannot reach the nurse, the phone tells the patient to
  go to the reception desk.
- **An ended visit refuses a press (409)** with "tell the reception desk now", instead of storing
  a press no nurse will see behind a false "the nurse has been told".
- **The EHR Flag records the press itself**, scheduled from the session's EHR patient before the
  rules run, so a rules failure no longer drops it. The event still goes out first (ADR-038).
- **Queue controls on the worklist.** "Call next" per department with patients waiting (the queue
  picks by confirmed level, then time waited), and for a called patient "Arrived", "No-show" and
  "Seen (free the room)"; closing or a no-show frees the room. The phone and the board follow over
  their live channels.
- **The sticky alert region takes at most 45% of the screen** and scrolls on its own, so the
  patient list and the queue controls stay usable with many alerts.

## Consequences

- A patient who deteriorates while waiting is visible on every nurse screen within about a second
  (1.16 to 1.18 s from the press to the worklist in the journeys), marked as new, without a reload.
- The worklist still reads each session's state one by one, so its refresh slows as a stack holds
  more open visits; M6-01 owns that.
- The staff app's Playwright stubs now keep state like the API (a completed task, a decision and
  saved vitals stay in later reads), which the live card needs.
