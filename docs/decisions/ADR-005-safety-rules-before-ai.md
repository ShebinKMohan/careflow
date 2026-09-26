# ADR-005: Safety rules run before any AI output

## Status

Accepted, 26 Sep 2026 ([M0-01]).

## Context

Emergencies (stroke, chest pain, sepsis, a drowsy child) must reach a nurse at once, and a model can be wrong, slow or unavailable. The UK regulator treats urgency suggestions as a medical device, so the design has to show that no model can suppress a danger sign and that a human decides every urgency level.

## Decision

- A plain-Python rules engine (`services/rules`: no I/O, no AI, versioned, property-tested with Hypothesis) runs on the **structured facts before any urgency suggestion** is produced. It raises alerts (FAST stroke, chest pain needing an ECG within 10 minutes, sepsis with a partial NEWS2 at the kiosk plus nurse inputs, NICE NG143 child flags) straight to the nurse.
- No model output can lower, remove or override a rules alert.
- The **nurse confirms every urgency level** (LangGraph `interrupt()`); the model only suggests.
- **No AI output reaches a patient as medical advice**; the phone RAG refuses medical questions and flags the nurse.
- Every fact in the doctor briefing carries a provenance tag (`EHR`, `PATIENT_STATED`, `DEVICE_MEASURED`, `NURSE_CONFIRMED`, `MODEL_INFERRED`).
- If extraction fails validation twice, the pipeline fails safe: the rules still run and the nurse sees "extraction incomplete".

## Consequences

- Alert latency is bounded by the rules engine, not by the LLM (target p95 <= 1000 ms, measured and published, never a CI gate).
- The rules engine is typed with pyright `strict` and must stay free of I/O and AI imports.
- These rules are never traded for speed or a green CI run.
