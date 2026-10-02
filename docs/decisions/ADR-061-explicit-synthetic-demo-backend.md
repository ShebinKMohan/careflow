# ADR-061: Explicit synthetic demo backend

Status: Accepted, 2026-10-02

## Context

The owner requires the existing CareFlow screens and options to remain intact.
The original Mac is unavailable; the Pi's measured model latency exceeds the
existing extraction deadline. The owner explicitly requested a demo backend
override and clear README disclosure.

## Decision

Add an opt-in `CAREFLOW_DEMO_MODE` for a synthetic development deployment.
Replace the model ports with bounded, deterministic adapters. Use the original
approved questions, source verification, summary checks and BM25 retrieval.
Retain the real API, authentication, consent, clinical rules, nurse confirmation,
session/queue state, QR lifecycle and synthetic FHIR integration.

Unrecognised descriptions are marked incomplete; no negative danger answers or
urgency levels are fabricated. Missing trained triage weights remain missing.
Voice retains its existing service and its measured limitations. Admin status
and the README explicitly identify the scripted behaviour. No automatic switch
from live inference is permitted. The flag is rejected outside `CAREFLOW_ENV=dev`.

## Consequences

The text workflow can be demonstrated without a GPU or model API charges.
This mode is not live AI, a clinical accuracy evaluation, or proof that every
feature is enabled. Public tunnel routing and Vercel configuration are separate
deployment work. The ordinary model configuration and all UI files are retained.
