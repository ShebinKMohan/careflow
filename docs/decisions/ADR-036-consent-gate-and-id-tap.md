# ADR-036: The consent gate and the mock ID tap

## Status

Accepted, 27 Sep 2026 ([M2-05]). This is the core build. The pytest cases, the two Playwright paths, the log-scan test and the back-translation check of `safety.consent.*` follow in the test phase: ADR-056, which also fixes what those tests found (a failed consent save was ignored, voice ignored data use, card numbers were in FHIR search URLs).

## Context

Filming or recording someone without consent is a legal and trust failure (PLAN feature #2). The kiosk had a microphone path (M1-14, M2-02) but no consent step. The contract had a consent route with no implementation. An ID tap was also needed as the second private check-in path: synthetic, and clearly a mock.

## Decision

### The consent screen comes before identification

- **The flow.** After the language, the kiosk shows one consent screen. Its text is `safety.consent.*` in English and Arabic:
  - the camera is processed on the kiosk and never saved;
  - voice is transcribed on the clinic server and no audio is kept;
  - answers go to the clinic team and the clinic record.
- **The three buttons.**
  - "Agree": camera, voice and data use.
  - "Continue without camera and voice": data use only. Keypad and tap intake still work.
  - "I don't agree to data use": the kiosk says "Please go to the reception desk." Nothing is checked in, so nothing reaches the clinic record: no session, no FHIR write.
- **When the choices are recorded.** The consent route needs a session, and sessions start at check-in. So the choices stay in the kiosk's memory until check-in succeeds, and are posted then, before anything else. They are never kept in browser storage, and a wipe forgets them. The check-in screen sends a patient without choices back to the consent screen.
- **Kiosk setup.** Signing in a kiosk now ends on the welcome screen instead of the check-in screen.

### Storage and the event

- **The table.** `POST /v1/sessions/{id}/consent` appends a row to `consent_records`. The row holds booleans, the text version and the session language. The table is clinic-scoped with forced RLS and the `clinic_isolation` policy. The app may select and insert, never update or delete.
- **Latest wins.** `get_consent()` returns the latest row. A session without a row has consented to nothing.
- **Reading it.** `GET /v1/sessions/{id}/consent` (patient token) returns the latest record, or 404.
- **The event.** Each record emits the internal event `consent.recorded {clinic_id, session_id, camera, voice}` through the outbox, for the audit log (M5-01). It is never a webhook.

### Gating in three places

Declining in any one of these closes the microphone:

1. **Kiosk.** `consent/hasConsent.ts` answers `hasConsent("camera" | "voice")`. The microphone button shows only with voice consent. `WsVoiceClient.start` refuses before `getUserMedia` without it. Camera code (M3-05) must call `hasConsent("camera")` first.
2. **API.** A voice turn (`input_mode: voice`) returns 403 without voice consent.
3. **Voice service.** At `start`, after the token check, the socket reads the session's consent from the API with the patient's token. Anything but a record with `voice: true` closes it with 1008, including an error reaching the API: the check fails closed.

Consent never allows storing camera frames or raw audio (safety rule 5). The media paths stay as they were.

### The mock ID tap

- **The reader.** `devices/card-reader.ts` defines the `CardReader` interface and a `MockCardReader`. With `NEXT_PUBLIC_SIMULATORS=1` (set in `Procfile.dev`), the check-in screen shows a panel labelled "Simulated ID reader". It lists the 7 personas' synthetic cards with masked numbers, and says the cards are synthetic and not a real ID service. No real Emirates ID or UAE Pass SDK or API is used.
- **The ids.** Each persona has a `mock_national_id` in `infra/seed/personas.yaml`: `784-<birth year>-<seeded 7 digits>-<Luhn check digit>`, synthetic. `infra/seed/add_mock_ids.py` (run by `make seed`, localhost only) writes it onto the HAPI Patient under `https://careflow.example/fhir/sid/mock-national-id`. It is idempotent. The persona bundles keep only their keypad id.
- **The route.** `POST /v1/checkin/id-tap` (kiosk token) takes `{clinic_id, kiosk_id, mock_card_payload: {card_type: "mock_national_id", id_number}, language}`.
  - It searches `Patient?identifier=<system>|<id>` through the same EHR reader as the keypad.
  - **One match:** the session is created exactly as by the keypad path (shared `open_session`).
  - **None:** 404 with "Please use the keypad."
  - **More than one:** 409 with "Please go to the reception desk."
  - Failed taps count against the kiosk's rate limit, like keypad guesses.
  - The id travels only in the body and is logged masked (`784-****-*******-C`).
- **Contract changes.** The request changes break oasdiff (new required consent fields, and the card payload changing from a string to an object). They are recorded in `oasdiff-err-ignore.txt`, because only the kiosk calls these routes and it changed in the same PR.

## Consequences

- A patient can refuse the camera and voice and still check in and answer by keypad and tap. A patient who refuses data use is sent to reception with nothing recorded.
- The microphone opens only with a recorded voice consent, checked in the kiosk, the API and the voice service.
- On the local stack (smoke run):
  - an unknown card gets 404 and the stroke persona's card checks in "F. A.";
  - a voice turn gets 403 before consent and 200 after it, and the latest consent wins;
  - two `consent.recorded` events reached the outbox;
  - the API log has no unmasked `784-` id.
- Pending for the test phase:
  - `pytest services/api -k "consent or id_tap"`: storage, the 1008 rejection, and 0, 1 and many matches;
  - Playwright (a): Agree, ID tap, voice intake;
  - Playwright (b): decline camera and voice, then keypad and tap intake with 0 `getUserMedia` calls;
  - the log-scan test;
  - no Encounter after a data-use decline;
  - the back-translation check of `safety.consent.*`.
