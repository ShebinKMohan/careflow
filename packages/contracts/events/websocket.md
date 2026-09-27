# Live channels (WebSocket)

Two channels push the same event envelope as the webhooks ([README.md](README.md), [`envelope.schema.json`](envelope.schema.json)). Each frame is one JSON envelope with its stream id: `{id, type, timestamp, data}`. After 20 seconds without an event, the server sends `{"type": "heartbeat", "timestamp": ...}`.

| Channel | Clients (roles) | Carries (always only the caller's clinic) |
|---|---|---|
| `/v1/ws/staff` | nurse station, doctor screen, queue board, admin (`nurse`, `doctor`, `board`, `admin`) | Nurse: `alert.*`, `triage.*`, `token.*`, `intake.*`, `patient.feels_worse`, `nurse.flag`. Doctor: `triage.confirmed`, `intake.completed`. Board: `token.called`, `token.no_show`. Admin: every event type. |
| `/v1/ws/sessions/{session_id}` | kiosk and companion phone (`kiosk_device`, `patient_session`) | That session's `token.called`, `token.no_show`, `intake.completed` and `session.handed_over` only: never an alert, a suggestion or an urgency level (patients see status, token and room). |

Internal events (for example `fhir.write.completed`) never reach a socket.

## Authentication: one-time tickets

1. The client calls `POST /v1/ws-ticket` with its bearer token and `{"channel": "staff"}` or `{"channel": "session", "session_id": "..."}`.
2. The API returns a single-use `ticket` that expires after **30 seconds** (M1-11).
3. The client connects to `wss://<host>/v1/ws/staff?ticket=...` (or the session channel).

A JWT or session token **never** goes in a URL or in a WebSocket frame; only the short-lived ticket does. A used or expired ticket closes the connection with code 4401. The server may close with 4403 when the caller's role may not use the channel.

The voice service's `WS /kiosk/audio` (M1-14, ADR-028) is not an API channel. It posts turns as the patient, so the kiosk sends the patient token in its first frame, never in the URL.

## Delivery

Every event is first written to the transactional outbox, then relayed to a Redis Stream that each API process reads (M1-11). Frames are best-effort and ordered per session. A reconnecting client may pass `?last_event_id=<id of the last frame it saw>` to replay what it missed, up to 5 minutes back. It should still re-read state through the REST API (`GET /v1/nurse/worklist`, `GET /v1/sessions/{session_id}/status`), which stays the source of truth when the live channel is down.

Research prototype. Synthetic data only. Not for clinical use.

## WebSocket-only frames

Some frames are for the screens only: they never become webhook events and have no schema in
`events/`. They are published straight to the event stream (not through the outbox) and have the
same shape as any frame, `{id, type, timestamp, data}`.

| Type | Channel | `data` | Meaning |
| --- | --- | --- | --- |
| `session.handed_over` | `/v1/ws/sessions/{session_id}` | `{"session_id": "..."}` | The patient claimed the session's QR hand-over on their phone (M2-06). The kiosk shows "Continued on your phone", forgets the patient and returns to the welcome screen; its tokens for the session no longer work. |
