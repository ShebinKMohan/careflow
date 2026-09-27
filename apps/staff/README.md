# staff

CareFlow's staff screens (Next.js, port 3001): the nurse station (M1-15), the doctor briefing, the queue board and the admin status page (M1-17 builds these out).

Research prototype. Synthetic data only. Not for clinical use.

## The nurse station

- **`/nurse`: the worklist.**
  - **Order:** danger first. Active critical alerts, then high alerts, then the level (no suggestion counts as 2), then arrival.
  - **Each row:** arrival time, initials, age and sex, complaint, alert chips, and a level chip, always "Suggested" until the nurse confirms it.
  - **Live updates** come over `/v1/ws/staff` with a one-time ticket. If the channel drops, the page says "Live updates paused", re-reads the list every 5 s and reconnects with backoff.
- **Alert banners** sit above everything:
  - they are silent and visual only (no sound, no screen-reader interruption);
  - each shows the rule, the fixed alert line, the evidence with each fact's provenance, when it was raised, and "Partial NEWS2" when partial;
  - the chest-pain rule adds the 10-minute ECG countdown and "ECG done";
  - banners stay until the triage is confirmed.
- **`/nurse/{session}`: the triage card.**
  - **Warnings.**
  - **The suggestion**, marked "Suggestion only. You decide.": the scale label, confidence, top 3 reasons and model version.
  - **Deciding:**
    - "Confirm level N" is never auto-focused, and Enter never decides;
    - "Change level" needs a level and a reason;
    - with no suggestion the nurse must choose.
  - **The facts**, each with its provenance.
  - **The token and room** appear only after the API accepts the decision.
- **The NEWS2 form** takes respiratory rate, systolic BP (a device reading is pre-filled, to confirm), ACVPU and oxygen. It shows the returned total, band and subscores, with "Partial" until all seven parameters are present.

## Running

```bash
make mock                                    # the Prism mock on :4010
pnpm -F staff dev                            # http://localhost:3001 (sign in at /login)
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 pnpm -F staff dev   # the real API
pnpm -F staff test                           # Vitest
pnpm -F staff test:e2e                       # Playwright: HTTP by page.route, the WebSocket by page.routeWebSocket
```

The Playwright fixtures in `tests/fixtures/api/` are checked against `openapi.yaml` by `packages/contracts/tests/test_app_fixtures.py`.
