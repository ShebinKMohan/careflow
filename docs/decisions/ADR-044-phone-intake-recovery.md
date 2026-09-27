# ADR-044: Recover phone intake from a revisioned server checkpoint

Status: accepted, 27 September 2026

## Context

The QR handover is single-use. Reloading its landing page discarded the current
question even though the phone still held its session credential. A network
failure during an answer is ambiguous: the server may have accepted the answer
before its response was lost. Retrying against a newly advanced question could
apply the same text twice. Comparing question IDs is insufficient because an
intake side question can return to the same question ID.

## Decision

Expose the pending patient question and an opaque, nonnegative `intake_revision`
in the authenticated session-status response. Read them under the runtime's
session lock. Return the revision on successful turn responses as well. Never
expose the nurse-review interrupt or urgency output to the patient.

Use the append-only transcript length as the revision of existing checkpoints.
It advances for pure side questions, unlike the capped clinical `turn_count`.
Clients copy this value unchanged. Phone answers send
`expected_intake_revision`; the runtime compares it and advances the graph under
the same session lock. A mismatch returns HTTP 409 without appending the answer.
The request field remains optional for existing kiosk and voice clients.

The phone stores only its credential and minimal visit identity in tab-scoped
session storage. It recovers the question from the server after reload and
reconciles uncertain responses before enabling another answer. It never caches
questions, answers or transcripts. Expired/terminal sessions clear credentials;
already-called visits use the current queue-status screen.

## Consequences

The API and companion are released together. Voice request serialization omits
unset optional fields rather than emitting contract-invalid nulls. Generated
contracts and valid test fixtures include the new response revision.

This protects answer retries once the browser has a session credential. It does
not solve a successful QR claim whose response was lost before that credential
reached the browser; that requires a separate server-side idempotent claim
protocol. Cross-app, physical-phone and live-model evidence remain distinct from
the focused recovery tests. See `docs/evidence/TAKEOVER-phone-recovery.md`.
