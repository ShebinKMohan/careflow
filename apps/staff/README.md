# staff

CareFlow's staff screens (Next.js, port 3001): the nurse station (M1-15), the doctor briefing, the queue board and the admin status page (M1-17 builds these out).

Research prototype. Synthetic data only. Not for clinical use.

## Workspace navigation and display

- The overview links to live role workspaces: nurse worklist, confirmed doctor sessions, queue board and system status. It does not deep-link to a fixture patient.
- Persistent navigation shows the current workspace. On narrow screens it becomes a horizontally scrollable navigation bar; a keyboard skip link goes directly to the main content.
- Sign-in stores the token in the current tab. The navigation offers sign-out when a token is present; sign-out clears it and returns to sign-in. API authorization remains the authority for access.
- The staff workspace uses Northstar General Hospital as an explicitly synthetic hospital identity, with warm white surfaces, navy text and cobalt actions. Clinical urgency colours keep their separate meaning.
- New reception check-ins carry a patient-stated registration, reason, visit purpose and department/doctor preference into the worklist and protected nurse/doctor detail. The list keeps initials; full registration name and contact appear in the protected detail only. The reception API is session-authorized; a missing legacy context stays quiet, and a failed fetch prompts a retry.
- Specialty and doctor choices are visibly **preferences, not bookings**. Actual queue assignment continues to follow nurse-confirmed urgency (`emergency` or `general`); the interface displays that assignment separately and does not promise specialist rooms or availability.
- The nurse worklist shows counts derived from its current response, a patient/complaint/token search and state filters. **Search and filters affect the table only. Safety alerts, worsening reports and medical questions remain visible.** Queue actions also continue to use the full worklist.
- Triage and NEWS2 share a two-column assessment layout on larger screens and stack on smaller ones. Suggestion labels, explicit nurse confirmation and fact provenance remain present.
- A rejected network request during sign-in, triage confirmation or saving vitals shows a failure state. Failed decision requests never display a success or token; failed vitals submissions retain the entered values.
- The public queue board reduces staff navigation and keeps its existing token/room-only data boundary and stale-connection warning.
- All navigation, search and filter labels have paired English and Arabic messages. Arabic remains machine-checked only, as stated by the prototype footer.

## Sourced clinical review

- The nurse session reads the live `/triage` and `/facts` APIs. The suggestion names its actual `model_id` and `model_version`, scale and level; each of the three reasons exposes its linked facts and source. A missing reference stays visible as **Source missing**. No model family is hardcoded as the active runtime.
- A clinical review groups the patient's main concern, onset, symptoms, pain, vital signs, recorded conditions, medicines and allergies. Source details retain conversation quotes, FHIR references, device/reading IDs, recording staff and model identity. Allergy criticality is retained.
- Missing measurements say **Not recorded**. Empty EHR lists mean no sourced records are available, not a negative medical history. A genuine zero pain score remains a recorded zero. Outstanding questions distinguish nurse assessment from patient/caregiver answers.
- Device readings in the demo are explicitly labelled simulated. Camera preview does not create observed clinical findings. Existing structured facts and their source are the authority for anything displayed.
- Failed facts refreshes preserve the last loaded facts and visibly mark them stale; an initial read failure says clinical details are unavailable. Changing sessions hides the previous patient's detail while the new request loads.
- Adaptive registration displays the caregiver separately from the actual patient and their medical record. An undecided route is awaiting assessment, and an empty registration reason directs staff to the sourced conversation. Identity matches are labelled demo records.
- Alerts show the real safety-rule identifier and ruleset version. These are distinct from the model's urgency scale and level; no unsupported hospital emergency code is fabricated.

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
