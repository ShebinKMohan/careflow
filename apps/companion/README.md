# Northstar General Hospital: patient check-in

Research prototype. Synthetic data only. Not for clinical use. English and Arabic are available; Arabic has not been validated by native speakers.

The phone journey finds or registers the person receiving care, asks permission, and opens the real adaptive intake conversation. A nurse remains responsible for clinical assessment and urgency confirmation. `NEXT_PUBLIC_HOSPITAL_NAME` changes the default **Northstar General Hospital** name.

## Start on a phone

1. Scan the welcome screen’s **Start on your phone** QR code. The link is `/start?l=en#t=…` (or Arabic). This is a scoped check-in link, not a patient account login.
2. Returning patients can choose mobile number or patient ID and explicitly allow record lookup. Only a masked possible match appears first. Verify the recorded year of birth before viewing full details and available history. For the local synthetic demo, patient ID `100001` has birth year `1968`; those keypad personas have no phone numbers. New patients can select **Register as a new patient** without a lookup.
3. Choose **Myself**, **A child**, **An older adult**, or **Another dependent**. Confirm the actual patient’s name, age, and sex, with optional phone. A verified adult supporting another patient remains the caregiver; their clinical history is never used as the dependent’s history.
4. Leave the department choice to the care team or select a preference from the labelled demo directory. A preference is not an appointment confirmation. Back retains draft values.
5. Allow use of the patient’s information for this visit. Voice is optional, and the camera remains off on the phone. Urgent symptom selections are sent with the initial visit request; the backend handles the nurse alert. The page also directs patients needing immediate help to reception.
   At any stage, **Get nurse help without finishing check-in** sends a consented, anonymous urgent request without waiting for patient details or vitals. The phone opens the nurse-review status only after the API confirms receipt.
6. **Start my check-in** calls `/v1/checkin/adaptive`. After creation, the phone reads the authoritative current question and revision, then sends real adaptive turns to `/v1/sessions/{id}/turns`. Question-specific hints are examples only and never prefill or submit patient facts.
7. After the conversation, `/status` shows the server’s nurse-review state and subsequent queue or room information. A visit does not automatically promise a queue number or diagnosis. **I feel worse** is also available during the intake.

## Optional local voice

Set `NEXT_PUBLIC_VOICE_ENABLED=true` and a reachable `NEXT_PUBLIC_VOICE_WS_URL` for the companion, then restart it. The default local URL is `ws://localhost:8090/kiosk/audio`; on a physical phone, localhost means the phone itself. Browser microphone capture requires HTTPS (or localhost). A phone using ordinary LAN HTTP receives a clear fallback and can type.

With explicit recorded voice permission, press and hold the microphone button (or Space outside a text field). Release stops capture and sends one utterance to the existing local voice service. That service transcribes and posts the answer; the phone never posts the transcript a second time. Before another answer, the phone reconciles the authoritative question and revision. Permission-prompt races, pointer cancellation, blur, and component cleanup stop capture. No browser cloud speech API is used.

## Existing visits and recovery

`/claim#t=…` continues an existing visit. The home page also has a secondary manual handover recovery field. Closing the tab discards its saved session; it does not revoke the clinical record.

- QR values, claim keys, identity challenges/proofs, full profiles, medical history, and unfinished drafts remain in memory. The URL fragment is removed immediately. An uncertain creation response retries the exact same body/key; editing is held until the result can be reconciled.
- Lookup and verification time out after 20 seconds; visit creation after 40 seconds. Rate limits allow another attempt after one minute. Reload before successful creation requires rescanning because unredeemed credentials are not persisted.
- Only a phone credential, session ID, initials, language, patient age category, and UI flow/voice flags are saved in tab-scoped `sessionStorage`. The server independently enforces consent and authorization. API responses are not cached by the service worker.
- Status uses the API and authenticated WebSocket with polling fallback and stale labels. Wait times are estimates.
- **I feel worse** posts the actual nurse alert. Its 10-second timeout directs the patient to reception on failure; unanswered drowsiness is `not_sure`. Its dialog traps and restores focus.
- **Ask the clinic** retains source citations, medical refusal, nurse referral, and bounded unavailable handling.

## Development and verification

Follow root `make up` for the complete stack. Set `CAREFLOW_PUBLIC_HOST` to the computer’s reachable address for a phone. The companion’s `/v1` rewrite forwards LAN requests to `CAREFLOW_API_URL`; the API still enforces authorization.

```sh
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 pnpm --filter companion dev
pnpm --filter companion test
pnpm --filter companion lint
pnpm --filter companion typecheck
```

Unit checks cover consent, masked lookup/verification, separate caregiver identity, actual adaptive payloads, retry identity, retained drafts, expired links, privacy boundaries, and microphone hold/release lifecycle. Fixture browser checks prove UI behavior only. Live model intake, audio transcription, physical-device permissions, staff response, and queue assignment need their own integration evidence.
