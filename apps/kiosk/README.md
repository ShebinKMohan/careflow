# kiosk

CareFlow's self check-in kiosk (Next.js, port 3000; M0-08, M1-03, M1-13).

Research prototype. Synthetic data only. Not for clinical use.

## The patient's flow

`/` welcome → `/checkin` silent keypad → `/intake` questions → `/vitals` simulated device → `/done` token and room.

- **Keypad.** Six digits, shown only as dots. The display opens no on-screen keyboard, and nothing is spoken on this screen. The digits go only in the body of `POST /v1/checkin/keypad`. After 3 misses the kiosk says "Please see reception."
- **Questions.** One at a time, in large text, with three ways to answer that are always on screen:
  - tap choices for the question's key: yes / no / not sure, 0–10, or the complaint grid;
  - a text box;
  - the mic, only with `NEXT_PUBLIC_VOICE_ENABLED=true` and in English (M1-14):
    - it streams to the voice service (`NEXT_PUBLIC_VOICE_WS_URL`, default `ws://localhost:8090/kiosk/audio`);
    - the service posts the spoken answer and speaks the next question;
    - the kiosk shows "You said: …" and the next question.

  A tap or text answer is a `POST /v1/sessions/{id}/turns` from the kiosk. It also stops listening, so an answer is never sent twice.
- **Done screen.** It polls the session status every 2 s and shows the token and room once assigned. It never shows a level or an alert, because the status route carries neither. In M1 the patient waits at the kiosk to see the token; M2-06 moves this to the patient's phone.
- **Idle timeout.** After 60 s without input the kiosk asks "Are you still there?". After 15 more seconds it wipes the session and returns to the welcome screen. It also starts over 30 s after the token is shown.

## Privacy

The patient's session (token, initials and the current question) lives in memory only: never in `sessionStorage`, `localStorage` or a cookie. The only thing the kiosk stores is its own device sign-in, in the tab's `sessionStorage` (`/setup`, staff only).

Voice audio goes straight from the microphone to the voice service and is never recorded. The session token travels only in the first WebSocket message. Leaving the intake screen, "Stop listening" and the idle wipe all stop the microphone.

## Running

```bash
make mock                                    # the Prism mock on :4010
pnpm -F kiosk dev                            # http://localhost:3000
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 pnpm -F kiosk dev   # the real API
pnpm -F kiosk test                           # Vitest
pnpm -F kiosk test:e2e                       # Playwright; the API is stubbed with tests/fixtures/api/
VOICE_E2E=1 pnpm -F kiosk test:e2e --project voice   # the real voice service, Chrome's fake mic (services/voice/README.md)
```
