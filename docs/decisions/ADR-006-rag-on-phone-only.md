# ADR-006: "Ask the clinic" RAG runs on the patient's phone only

## Status

Accepted, 26 Sep 2026 ([M0-01]). Owner decision of 26 Sep 2026.

## Context

The plan includes an "ask the clinic" assistant that answers questions about hours, departments, insurance, visitors, pharmacy and parking from the clinic's own documents. The kiosk is a shared, public screen with a roughly two-minute intake: questions asked there slow the queue, can be overheard, and invite wayfinding or visitor use that the triage flow is not built for. Patients mostly have questions while they wait.

## Decision

- The RAG assistant runs **only on the patient's phone**, in the companion PWA, after the QR hand-over from the kiosk.
- The kiosk has **no** ask-the-clinic mode. A kiosk visitor or wayfinding mode is deferred to a later stage and is out of scope for this build.
- On the phone, every answer cites its source, says "I don't know" when unsure, and **never answers a medical question**: it refuses and flags the nurse. Mid-intake questions get a brief answer and the intake resumes.

## Consequences

- The kiosk intake stays short and private.
- The medical-refusal rate (target 100%) and citation correctness are evaluated on the phone flow only.
- A later kiosk mode would need its own decision record, privacy review and evaluation.
