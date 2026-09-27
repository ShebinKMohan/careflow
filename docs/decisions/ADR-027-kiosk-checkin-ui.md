# ADR-027: The kiosk check-in and intake UI

## Status

Accepted, 27 Sep 2026 ([M1-13]).

## Context

The kiosk is the first thing a patient sees. They check in without saying anything aloud and answer questions by tapping, typing or speaking. They leave with a token and room, and nothing about them stays on the kiosk.

Two contract gaps stood in the way: M0-03's check-in and turn responses carried no question key, so the kiosk could not pick tap answers, and they had no place for the nurse-coming notice that M1-07 produces.

## Decision

### State

- **The patient lives in memory only.** The session is React state in `KioskProvider`, in the root layout: token, initials (`patient_display`) and the current question. It is never in `sessionStorage`, `localStorage` or a cookie. Client-side navigation keeps it.
- **`wipe()` forgets the patient** and runs registered clean-ups (the voice client's `abort()`). It runs:
  - on the welcome screen;
  - on the idle timeout;
  - 30 s after the token is shown.
- **The kiosk's own sign-in** (a `kiosk_device` token, not patient data) stays in the tab's `sessionStorage`, so a reload keeps the kiosk signed in.
- **Two clients.** Kiosk calls (check-in, vitals) use the device token. Patient calls (turns, status) use the in-memory `patient_session` token.

### Contract (additive)

- **`IntakeQuestion`** is `{question_id, text, key, notice}`:
  - `key` is the StructuredFacts path the question asks about, or null for a message the patient only acknowledges;
  - `notice` is shown before the question.
- **Where it appears.** `CheckinResult.first_question` and `TurnResponse.next_question`, where null means the conversation is done. Both are optional, and oasdiff reports no breaking change. M1-12 fills them from the graph's `Question`.
- **Fallbacks** keep the kiosk working with an older response:
  - without `first_question`, the kiosk asks the M1-07 chief-complaint template;
  - without `next_question`, `assistant_text` becomes a question without a key, and `done` ends the conversation.
- **Input modes** are the contract's `input_mode` values `tap | text | voice`, which the issue calls `source: tap | typed | voice`.

### Screens

- **Keypad** (`components/Keypad.tsx`):
  - digits 0–9, backspace, clear, and OK once 6 digits are in;
  - shown as dots in a read-only field with `inputMode="none"`, so no on-screen keyboard opens;
  - no audio.

  Each failure mounts it again, empty. After 3 failures the kiosk shows "Please see reception." and the keypad stays disabled until the idle wipe.
- **Questions** (`components/IntakeFlow.tsx`): the question is in `text-title` (32 px), with the notice above it. Tap answers come from `lib/quickAnswers.ts` by key:

  | Key | Tap answers |
  |---|---|
  | `chief_complaint` | the complaint grid (chest pain, breathing, fever, injury, pain, other) |
  | `pain_score`, `severity` | 0–10 |
  | the FAST, chest-pain, infection and child screens, `pain_present`, `injury`, `allergies_confirm` | yes / no / not sure |
  | times and places (`symptom_onset_at`, `pain_location`, `chest_pain.onset`, `fast.last_known_well`) | none |
  | a message (no key) | OK |

  A tap sends the answer's label in the session language. The text box and the mic, when enabled, are always on screen too. A test reads the 23 keys from M1-07's `templates_en.yaml`, so the two cannot drift.
- **Vitals:** the M1-03 panel, with `?scenario=` passed through. "Skip" becomes "Continue" once a reading arrives.
- **Done:** it polls the status every 2 s with the patient's token. It shows "Please take a seat…", then the token and room once assigned, and the wait estimate labelled as an estimate. The status route never carries a level or an alert (safety rule 2).
- **Idle** (`lib/useIdle.ts`, `components/IdleGuard.tsx`):
  - input events anywhere on the page reset a 1 s tick measured against `Date.now()`, so Playwright's fake clock can jump ahead;
  - after 60 s, an `alertdialog` asks "Are you still there?" with a 15 s countdown and "I'm still here";
  - then the kiosk wipes and returns to `/`;
  - only the patient screens (`/checkin`, `/intake`, `/vitals`, `/done`) time out.
- **Voice** (`lib/voice/`): the `VoiceClient` interface (`start`, `stop`, `abort`) with `NoopVoiceClient`. The mic button shows only with `NEXT_PUBLIC_VOICE_ENABLED=true`. M1-14 implements the client.

### Tests

- **Vitest:**
  - keypad masking, maximum length, backspace and clear;
  - the tap-answer mapping, checked against every M1-07 key;
  - the session held in memory only, and `wipe()` running its clean-ups once.
- **Playwright:** the API is stubbed with `page.route()` fixtures in `tests/fixtures/api/`, because Prism serves one example per route and cannot drive a multi-step intake. Prism stays for the smoke tests. The happy path also measures every button (at least 56 px each way) and the reset 30 s after the token.

## Consequences

- **M1-12** returns `first_question` and `next_question`, and adds CORS for the kiosk origin (ADR-015).
- **M1-14** implements `VoiceClient`, and the kiosk turns voice on with `NEXT_PUBLIC_VOICE_ENABLED`.
- **M2-01** adds Arabic right-to-left layout: the Arabic strings exist (machine-written, not reviewed by a native speaker).
- **M2-05** adds the consent gate.
- **M2-06** moves the token to the patient's phone.
