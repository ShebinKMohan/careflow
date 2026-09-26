# ADR-002: Contracts first; generated types are never edited by hand

## Status

Accepted, 26 Sep 2026 ([M0-01]).

## Context

Six lanes (integration, voice and conversation, on-device ML, apps, safety and eval, platform) are built at the same time by different agents. Without one agreed shape for the structured facts, the urgency suggestion, alerts, events and REST bodies, each lane would invent its own and the wiring phase would turn into rework. The doctor briefing also needs every fact to carry its provenance, which only works if one schema carries the tag everywhere.

## Decision

- `packages/contracts` is the single source of truth: OpenAPI 3.1 (`openapi.yaml`) for routes, roles and bodies; JSON Schemas for the structured facts and events; the FHIR R4 mapping table.
- Python models are generated with `datamodel-code-generator`, TypeScript types with `openapi-typescript`. Generated files are never hand-edited; CI checks that they match the sources.
- Every LLM output is validated against a Pydantic model from `packages/contracts`, whatever the provider.
- "Contracts win": a missing route, role or field is added to the contract, regenerated and noted in the pull request. No lane works around the contract.

## Consequences

- Apps can be built against a mock API (Prism) generated from the same OpenAPI file before the real API exists.
- A contract change is a visible, reviewable diff with its regenerated types in the same commit.
- Changes to `packages/contracts/` serialise parallel work: two issues that touch it never run concurrently (`docs/AGENT_RUNBOOK.md` section 1).
