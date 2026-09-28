# Northstar reception rebuild — 28 September 2026

**Final result:** both real-stack browser journeys passed after the final reload: **2 passed in 21.4 seconds**. The kiosk journey includes a nurse decision and a real queued ticket; the phone journey includes the rendered server receipt and waiting-for-nurse status. Focused E2E TypeScript checking and ESLint also passed.

This evidence covers the new structured registration journey, separately from the earlier conversational UI rebuild. It uses fictional patient details and the real local API, Postgres, Valkey and HAPI stack. No browser API responses were mocked.

Run the focused check against the running stack:

```sh
pnpm --filter @careflow/e2e exec playwright test --config reception.config.ts
```

The test covers kiosk consent, new patient details, twelve departments, synthetic doctor preference, visit reason, review/Edit, saved demo vital readings, automatic completion, the same visit appearing in the nurse worklist and reception summary, the nurse’s accepted level 4 decision, and a real queue ticket reaching the patient. It also verifies that the Back action displays the saved patient details and returns to the health-check screen. A second test uses a real entry ticket issued to the kiosk, registers on a 390 px phone viewport and checks the waiting screen. Both verify Back retains entered data and that there are no conversational `/turns` requests. Every captured stage is checked for horizontal overflow.

The kiosk stores a server-managed station marker, not a device login entered by the patient. The phone starts from an expiring QR ticket, with no patient account or device login. The browser test transfers the issued ticket to the phone route; this is not a physical camera scan test.

Screenshots are in [screenshots](screenshots). Important stages: [desktop departments](screenshots/kiosk-departments.png), [doctor preference](screenshots/kiosk-doctors.png), [registration review](screenshots/kiosk-review.png), [completed kiosk registration](screenshots/kiosk-complete.png), [nurse reception review](screenshots/nurse-reception-review.png), [phone review](screenshots/phone-review.png), and [phone waiting](screenshots/phone-waiting.png).

The final run recorded these local timings; [stage-timings.json](stage-timings.json) contains the raw numbers:

| Observed operation | Time |
| --- | ---: |
| Kiosk details → departments |45 ms|
| Kiosk reason → review |41 ms|
| Kiosk registration request |230 ms|
| Guided measurement through saved completion |10,456 ms|
| Nurse decision request and confirmation |457 ms|
| Confirmed decision → queued status response |144 ms|
| Phone details → departments |37 ms|
| Phone registration request |122 ms|

An additional live check exposed a backend delay after nurse confirmation: patient status requests waited 16.4–16.7 seconds. The reception briefing path now uses deterministic content without an optional model call; after reloading that change, the fresh nurse decision and patient queue checks passed at the timings above. No wait-time estimate was substituted for an actual queued token. See [queue ticket](screenshots/kiosk-queue-ticket.png), [saved visit summary](screenshots/kiosk-saved-visit.png), and [measurement in progress](screenshots/kiosk-vitals-measuring.png).

Raw Playwright reports and traces are ignored by Git because they may contain ephemeral entry tickets or development authorization. The curated screenshots contain only synthetic patient details. Stage timings are local measurements from a single run, not throughput or production latency claims.

A live integration check caught and fixed the phone's consent-version format: the API permits a date with an optional numeric revision; `2026-09-28.reception` was rejected with HTTP 422. The phone now sends `2026-09-28.1`. The browser test executes the real API contract so this failure is not hidden by response fixtures.

The reception runtime starts after the conversational extraction stage and deliberately provides no model urgency suggestion. Backend tests independently make the conversational, extraction and urgency model entry points throw and still reach nurse review. Unchecked urgent symptoms do not become explicit negative clinical facts. Department and synthetic doctor selections are preferences, not confirmed appointments. Demo vital signs are simulated; this run does not validate medical devices, clinical performance, deployment readiness or real patient use. The phone queue screen remains awaiting nurse review until a clinician makes the actual decision.
