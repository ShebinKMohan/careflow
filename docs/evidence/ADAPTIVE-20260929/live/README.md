# Final local nonvoice journey evidence

**Final result: the returning-adult, caregiver-child, and caregiver-elder browser journeys all passed on the restarted local stack. The existing new-adult visit also passed its resumed nurse confirmation and FHIR check.** No further model calls or tests were run after these passes. Voice work was paused at the user's request and is outside this final verification.

## Final results

| Case | Browser result | Actual nurse result | Exact FHIR encounter subject |
| --- | --- | --- | --- |
| New adult, ankle injury | Interview, simulated vitals and real Laya assessment observed in `compact-run/new-adult.json`; same visit's final staff handoff passed in 4.3 s | Confirmed KTAS 4; token **G-015** | Encounter `198621` → `Patient/198620` |
| Returning adult, chest pain | Full journey passed in **31.6 s** | Confirmed KTAS 2; token **A-021** | Encounter `198633` → `Patient/persona-routine` |
| Child with separate caregiver | Full journey passed in **20.4 s** | No adult model; nurse selected KTAS 3; token **A-022** | Encounter `198648` → `Patient/198647` |
| Elder with separate caregiver | Full journey passed in **60.0 s** | Confirmed KTAS 3; token **A-023** | Encounter `198655` → `Patient/198654` |
| Anonymous urgent help | Passed twice; latest run **22.8 s** | Existing `CHEST_PAIN_ECG_10MIN` rule; no model; nurse selected KTAS 3; token **A-020** | Anonymous visit intentionally has no patient EHR writeback |

The final three complete journeys ran serially from 19:34 to 19:36 UTC on 2026-09-28 (2026-09-29 local time). They used actual local `qwen3.5-9b-4bit` extraction and `laya-ktas-ebf31b5e-s42` for the adult urgency suggestions. Final extraction status was `complete` in all three. Each adult suggestion returned three reasons, and every referenced fact ID existed in that visit's sourced facts.

Observed first natural-answer response times were **11.145 s** for the returning adult, **14.922 s** for the child, and **10.593 s** for the elder. The earlier successful ankle interview took **16.175 s** for its opening answer. These are measured local responses, not an instant-response or production-performance claim.

The child supplied a natural opening complaint before the remaining mandatory safety questions. Facts already extracted from that answer did not require duplicate questions. The child bypassed adult sensor measurements and adult urgency prediction. The final Encounter belonged to the child, not the verified caregiver. The elder likewise retained a separate helper.

The resumed ankle check used the original synthetic session `d4c813ba-73ef-598f-8c34-3b5150d64ead`. It confirmed the actual stored suggestion through the staff UI, issued its token, and verified exactly one Encounter for the actual new patient. The original patient browser context had closed on the earlier test failure, so **no post-confirmation patient receipt screenshot is claimed for that resumed case**. The other three complete journeys did verify both the patient token and the nurse-confirmed receipt.

## Scope and artifacts

These checks use the running local kiosk, API, staff app, HAPI, queue, and model services. They do not stub network responses, reset the queue, or restart services. All patient data is synthetic. Device readings are simulated and labelled as such. This verifies local workflow behavior; it is not clinical validation, physical sensor verification, or proof of a deployed backend.

- `new-adult-resumed.json`, `returning-adult.json`, `caregiver-child.json`, and `caregiver-elder.json` contain the final scoped evidence.
- `screenshots/` includes patient identity, conversation, measurements, receipts, and actual nurse review/confirmation screens.
- `initial-run/` preserves the initial incomplete extraction attempts and the two corrected test assumptions.
- `compact-run/new-adult.json` preserves the successful ankle extraction and model output before its staff UI race was fixed.
- Private browser traces and runner reports stay in the ignored `.run/adaptive-live/` directory. Patient session tokens are not written into these public evidence JSON files.

Individual case files retain their own timestamps. `stage-results.json` contains the latest worker's aggregate, so prefer the case files when examining different runs.

## Fixes established during the checks

The first long ankle and elder descriptions reached the 20-second extraction deadline. The runtime now preserves the exact complaint with an incomplete-extraction warning on failure; the successful final checks above still required actual completed extraction. The earlier failure evidence remains archived.

The browser tests also exposed a staff race: a temporary absent suggestion opened a default override form, which stayed open after the measured-vitals suggestion arrived. The fix distinguishes an untouched fallback form from a nurse's explicit edit. It exposes confirmation when an untouched suggestion arrives, preserves the nurse's selected level and exact edited reason, and removes the stale default explanation. **18 targeted staff tests, staff typecheck, and targeted lint passed** before the final browser checks.

The queue receipt now refreshes immediately when its token arrives; the final full journeys asserted the nurse-confirmed label as well as the token before taking screenshots. The child opening-complaint fix preserves the compulsory safety bank.

## Reproduce against an existing local stack

```sh
pnpm --filter @careflow/e2e exec playwright test --config adaptive-live.config.ts
```

The preserved-visit resumption test is skipped in ordinary runs. It was executed once with `CAREFLOW_RESUME_OWNED_VISIT=1` and a `--grep 'resume owned ankle'` filter while that owned synthetic visit was still awaiting confirmation. It must not be treated as a repeatable new-visit scenario after confirmation.
