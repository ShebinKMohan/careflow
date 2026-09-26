# CareFlow events

CareFlow publishes the same events to hospital systems (webhooks, M4-03) and to its own screens (WebSockets, M1-11; see [websocket.md](websocket.md)). They are listed under `webhooks:` in [`../openapi.yaml`](../openapi.yaml).

| Type | Data schema | When |
|---|---|---|
| `alert.raised` | `alert-raised.schema.json` | A safety rule raised an alert (before any urgency suggestion). |
| `triage.suggested` | `triage-suggested.schema.json` | A model suggested a level (reasons omitted: model text). |
| `triage.confirmed` | `triage-confirmed.schema.json` | The nurse confirmed or overrode the level; it is now final. |
| `token.called` | `token-called.schema.json` | A queue token was called to a room. |
| `intake.completed` | `intake-completed.schema.json` | The intake conversation finished. |
| `patient.feels_worse` | `patient-feels-worse.schema.json` | The waiting patient pressed "I feel worse". |
| `nurse.flag` | `nurse-flag.schema.json` | Something needs a nurse, e.g. a refused medical question. |

## Internal events

These travel only on the CareFlow event bus (M1-11), for example to the audit log (M5-01). They are never sent as webhooks or WebSocket messages, so they are not in `envelope.schema.json`'s type list or under `webhooks:`. `careflow_contracts.events.INTERNAL_DATA_MODELS` lists them.

| Type | Data schema | When |
|---|---|---|
| `fhir.write.completed` | `fhir-write-completed.schema.json` | CareFlow wrote a confirmed triage to the hospital FHIR server (M1-09). |

## Payload

Every payload is the [Standard Webhooks](https://www.standardwebhooks.com/) envelope in [`envelope.schema.json`](envelope.schema.json):

```json
{"type": "token.called", "timestamp": "2026-09-26T09:08:00Z",
 "data": {"clinic_id": "clinic_demo", "session_id": "ses_chest_pain", "token_id": "tok_chest_pain",
          "token_code": "A-014", "department": "emergency", "room": "Resus 2", "called_at": "2026-09-26T09:08:00Z"}}
```

`data` always carries `clinic_id` and `session_id`, and **never a name, a date of birth or free text** (no notes, no transcript quotes, no model reasons). Consumers fetch details through the API with their own credentials.

## Headers

| Header | Value |
|---|---|
| `webhook-id` | Unique event id (a ULID). Identical on every retry, so receivers can deduplicate. |
| `webhook-timestamp` | Unix time in seconds when the message was signed. |
| `webhook-signature` | `v1,<signature>`: base64 of HMAC-SHA256 over `"{webhook-id}.{webhook-timestamp}.{body}"`, keyed with the subscriber's secret (`whsec_...`, base64 after the prefix). Several space-separated signatures may appear during secret rotation. |

Receivers must verify the signature over the raw body, reject timestamps more than 5 minutes from their clock (replay window), and treat a repeated `webhook-id` as already processed. Delivery (outbox, retries with backoff, dead-letter queue) is built in M4-03.

Research prototype. Synthetic data only. Not for clinical use.
