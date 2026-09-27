# Live channels (WebSocket)

Two channels push the same event envelope as the webhooks ([README.md](README.md), [`envelope.schema.json`](envelope.schema.json)). Each frame is one JSON envelope `{type, timestamp, data}`.

| Channel | Clients (roles) | Carries |
|---|---|---|
| `/v1/ws/staff` | nurse station, doctor screen, queue board (`nurse`, `doctor`, `board`) | Every event for the caller's clinic. The board receives only `token.called` and `token.no_show`. |
| `/v1/ws/sessions/{session_id}` | kiosk and companion phone (`kiosk_device`, `patient_session`) | Events for that session only, filtered for patients: never an alert or an urgency level (patients see status, token and room). |

## Authentication: one-time tickets

1. The client calls `POST /v1/ws-ticket` with its bearer token and `{"channel": "staff"}` or `{"channel": "session", "session_id": "..."}`.
2. The API returns a single-use `ticket` that expires after **30 seconds** (M1-11).
3. The client connects to `wss://<host>/v1/ws/staff?ticket=...` (or the session channel).

A JWT or session token **never** goes in a URL or in a WebSocket frame; only the short-lived ticket does. A used or expired ticket closes the connection with code 4401. The server may close with 4403 when the caller's role may not use the channel.

## Delivery

Frames are best-effort and ordered per session. After a reconnect, clients re-read state through the REST API (`GET /v1/nurse/worklist`, `GET /v1/sessions/{session_id}/status`) rather than relying on missed frames.

Research prototype. Synthetic data only. Not for clinical use.
