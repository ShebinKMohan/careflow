# Companion identity and adaptive journey — 2026-09-28

The companion now supports the following implemented paths:

- QR → returning record lookup with explicit data permission → masked match → birth-year verification → patient details/history → visit recipient → optional department → consent → `/v1/checkin/adaptive` → authoritative intake checkpoint → actual intake turns → nurse review/status.
- QR → new patient → actual patient and optional supporting-person details → optional department → consent → adaptive intake.
- QR → immediate anonymous nurse help with explicit permission and optional symptoms → `/v1/checkin/urgent` → confirmed session status. No identity, vitals, or model step is required first.
- During intake, the existing nurse-alert action remains available; the patient receipt displays the actual server assistance flag, rule alerts, visit reference, facts/provenance, and provisional-versus-confirmed urgency.

A verified adult's identity moves to `caregiver.identity_token` when registering another patient. The actual patient gets separate blank fields. Their record/history is not inherited. Identity proof, full names/history, draft values, QR and claim key stay out of session storage. An uncertain create response uses the same endpoint/body/key on retry.

## Verification actually run

- `pnpm --filter companion test`: **60 passed across 8 files**.
- `pnpm --filter companion typecheck`: passed.
- `pnpm --filter companion lint`: passed.
- Chrome at 390×844, fixture adaptive path: passed. Covered retained Back data, optional department, final consent, API request shape, server question rendering, typed turn with server revision, nurse-review status, visit reference, no horizontal overflow, and session storage content boundaries.
- Chrome at 390×844, **live identity path**: passed against the running local API. A real QR came from the kiosk BFF; the phone lookup returned HTTP200 for seeded patient ID100001; verification returned HTTP200 for birth year1968. Full details/history were shown after verification. Switching to a child cleared actual-patient details, preserved the adult solely as caregiver, and did not display the adult history. No adaptive/model request was made in this identity test.

Screenshots are separated by their evidence source:

- `live/phone-returning-patient.png`
- `live/phone-dependent-separation.png`
- `fixtures/phone-consent.png`
- `fixtures/phone-question.png`
- `fixtures/phone-nurse-review.png`

The existing-server browser config avoids a production build colliding with the live dev server:

```sh
pnpm --filter companion exec playwright test tests/e2e/mobile-start.spec.ts --config=playwright.live.config.ts
CAREFLOW_LIVE=1 pnpm --filter companion exec playwright test tests/e2e/identity-live.spec.ts --config=playwright.live.config.ts
```

## Voice and proof boundaries

The companion ports the local hold-to-talk client and PCM worklet. Unit checks cover one submission on release, permission races, cancellation/blur, non-interference with typed fields/dialogs, explicit consent, and capture cap. After a voice turn, the phone reads the authoritative checkpoint before another answer; the transcript is not posted twice. A stalled processing response cancels after45seconds and reconciles. The voice service remains the authority for recorded consent and posting turns.

Companion audio capture itself has not been proven with physical microphone hardware in this pass. It requires `NEXT_PUBLIC_VOICE_ENABLED=true`, a reachable local voice websocket, and a secure browser context. Ordinary LAN HTTP on a phone cannot use getUserMedia; the UI states that limitation and retains typing. The live identity test and fixture adaptive test do not prove real Qwen/ASR, staff acknowledgement, queue assignment, or clinical correctness. Those need separate integration evidence.
