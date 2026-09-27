# ADR-037: The QR hand-over from the kiosk to the phone

## Status

Accepted, 27 Sep 2026 ([M2-06]). This is the core build. The security pytest cases, the two-context Playwright journey and the network-capture check follow in the test phase.

## Context

Patients should not have to say their symptoms aloud in the lobby, and the kiosk should be free sooner. The QR hand-over is the third private check-in path (PLAN feature #1). It also opens the phone-only features: the waiting room (M2-07) and "ask the clinic" (M2-09). The contract already named the two routes, and `.env` already had a generated `CAREFLOW_HANDOVER_SIGNING_KEY`, but nothing used them.

## Decision

### The token

- **Signing.** `careflow_api/handover.py` issues a PyJWT HS256 token signed with its own key (`CAREFLOW_HANDOVER_SIGNING_KEY`), never the JWT secret.
- **Claims.** `sid`, `clinic_id`, `jti`, `aud = "careflow-companion"`, `iat`, and `exp = iat + 120 s`. The token carries no personal data.
- **Verification.** It is checked with 10 s of leeway for clock skew, and must carry the audience and every claim. Without the key, both routes answer 503.
- **Where it travels.** The kiosk shows `http://<companion>/claim#t=<token>`. The token sits in the URL fragment, so no browser sends it in a request or a log. The companion reads the fragment, clears it with `history.replaceState`, and POSTs it. It never reaches an API log: the smoke run found none.
- **No query strings.** The old `?code=` pre-fill on the companion's code form is gone, so a hand-over token never travels in a query string.

### The routes

- **Issuing: `POST /v1/sessions/{id}/qr`.**
  - Callers: the kiosk's device token (only the kiosk that checked the session in), or the session's own kiosk-channel patient token.
  - A phone token gets 403. A closed or already handed-over session gets 409.
- **Claiming: `POST /v1/checkin/qr/claim`.** It needs no login and is rate-limited to 10 claims a minute per IP.
  - 401: an expired, forged or wrong-audience token.
  - 409: a spent token (`SET handover:jti:<jti> 1 NX EX 300`), or a closed or already handed-over session.
  - 200: a phone-channel patient token, and the question the intake is waiting on. The question is read from the graph's `pending_question`, which survives any state update, unlike the interrupt's value.

### Channels and revocation

- **The claim.** Patient tokens carry `channel`: keypad and ID tap issue `kiosk`; the claim issues `phone`. A token without the claim counts as `kiosk`. `require_phone_session()` admits only phone tokens; M2-07 and M2-09 use it. Every session route already checks the token's session against the path.
- **Revocation: a per-session cutoff instead of revoking one `jti`.**
  - What the issue asked: revoke the kiosk token's `jti` in Redis.
  - Why that doesn't work: the kiosk may ask for the code with its device token, so the patient token's `jti` isn't known at claim time, and no token `jti` was ever recorded.
  - What we do: the claim sets `handover:session:<sid>` (2 hours, longer than any patient token). `get_principal` then refuses every kiosk-channel patient token of that session with 401 ("This session continued on the patient's phone.").
  - Result: the same effect, covering every kiosk token of the session.
  - The voice socket goes through the API for the consent check (ADR-036), so a revoked kiosk token cannot open the microphone either.

### Resuming

- **The graph.** It is unchanged: `thread_id = session_id` and the Postgres checkpointer. The claim sets the state field `channel = "phone"`.
- **Phone answers.** They go through the same routes, graph and rules, so safety rules 1 and 3 still apply.
- **The companion.**
  - `/claim` claims the session and switches the app to the session's language.
  - It shows the pending question with the kiosk's tap choices and a text box, and continues with `POST /v1/sessions/{id}/turns`.
  - The token and session stay in memory and `sessionStorage`, never in `localStorage`.
  - Voice on the phone is not built yet: text and tap only. The consent recorded on the session would apply to it.

### The kiosk

- **The panel.** Every intake screen has "Continue on your phone". It shows a `qrcode.react` (4.2.0, ISC, pinned) code with a countdown, and fetches a new code when it runs out.
- **Listening for the claim.** While the code is shown, the kiosk listens on `/v1/ws/sessions/{id}` (one-time ticket, never a token in the URL).
- **The frame.** `session.handed_over {session_id}` is a WebSocket-only frame:
  - it is published straight to the event stream, never through the outbox, so it never becomes a webhook;
  - it is documented in `events/websocket.md`;
  - it is added to the session channel's allowed types.
- **Forgetting the patient.** On that frame the kiosk shows "Continued on your phone" for 5 s, then wipes the patient (memory only) and returns to the welcome screen.

## Consequences

- A patient can move the intake to their own phone at any point. The kiosk keeps nothing about them, and its tokens for the session stop working at once.
- On the local stack (smoke run):
  - a forged code gets 401, a valid claim 200 (with the pending question), and a replay 409;
  - the kiosk's socket received `session.handed_over`;
  - the old kiosk token gets 401 on `/turns` while the phone token works;
  - a phone can't issue a code (403) or reach another session (403);
  - a second code after the hand-over gets 409.
- Pending for the test phase:
  - the pytest security cases: expired 401, replay 409, closed 409, cross-session 403, revoked kiosk token 401, and `require_phone_session` rejecting device and kiosk tokens;
  - the two-context Playwright journey (Pixel 7): 2 answers on the kiosk, then the phone finishes, and the kiosk is back on the welcome screen within 6 s;
  - the network capture showing the token only in the claim body.
