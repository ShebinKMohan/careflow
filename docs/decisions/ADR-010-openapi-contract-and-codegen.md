# ADR-010: OpenAPI contract, events and code generation

## Status

Accepted, 26 Sep 2026 ([M0-03]).

## Context

`packages/contracts/openapi.yaml` (OpenAPI 3.1) is the single source for CareFlow's routes, roles, bodies and outbound events. It reuses the M0-02 JSON Schemas by `$ref`, and five tools read it: Redocly (lint), openapi-typescript (TS types), datamodel-codegen (Python models), oasdiff (breaking changes) and Prism (mock, M0-08). The issue left several details open, and the tools disagree on how external references resolve.

## Decision

- **Roles** are declared per operation in `x-careflow-roles` (`kiosk_device`, `patient_session`, `nurse`, `doctor`, `admin`, `board`, `integration`). A bearer JWT is required everywhere except `GET /health`, the two token endpoints (`/v1/auth/login`, `/v1/auth/device-token`) and `POST /v1/checkin/qr/claim`, whose single-use `qr_token` in the body is itself the credential (the phone has no bearer yet).
- **Every response body is a named component** with an example taken from the M0-02 persona fixtures, and errors are RFC 9457 problem details. `TriageView` adds `needed_facts` and the optional `decision`; the decision route returns `DecisionResult {decision, token}` because confirming a level is what issues the queue token.
- **Events** use the Standard Webhooks payload `{type, timestamp, data}`; the event id travels in `webhook-id`. Data schemas omit every free-text field (suggestion reasons, override notes, feels-worse notes). Data schema files use hyphens (`alert-raised.schema.json`) because code generators treat dots as module separators; the event types keep their dots. On the session WebSocket channel patients never receive alerts or levels, and the board receives only `token.called`.
- **No `$id` in the JSON Schemas**, and every root type is also addressable as `#/$defs/<Name>`. Relative `$ref`s therefore resolve by file location in every tool, and all external references are fragment references, which datamodel-codegen's `--external-ref-mapping` needs.
- **Python models**: `api_models.py` is generated from `openapi.yaml` with `--external-ref-mapping`, so the M0-02 and event models are imported, never duplicated (one `StructuredFacts` class everywhere). `--collapse-root-models` is replaced by `--use-type-alias` alone, because collapsed aliases cannot be imported across modules. `make contracts` runs `packages/contracts/codegen/generate.sh`.
- **Redocly** extends `recommended` with 0 errors required. `info-license` is off (the owner has not chosen a licence) and `no-invalid-media-type-examples` is off: Redocly's bundled validator rejects the valid `StructuredFacts` example (mis-evaluated `unevaluatedProperties` with the provenance `if`/`then`), while the reference `jsonschema` implementation and stock ajv 2020 accept it. `tests/test_openapi_examples.py` validates and parses every example instead. Two deliberate exceptions sit in `.redocly.lint-ignore.yaml`.
- **Breaking changes**: `scripts/contracts_breaking.sh` extracts `packages/contracts` from the base ref (so relative refs resolve) and runs `oasdiff breaking --fail-on ERR`; it skips when the base has no spec. A new CI job `contracts` runs lint, regenerate-and-diff and the breaking check.

## Consequences

- A later issue that needs a route or field adds it to `openapi.yaml` first, regenerates and notes it in its PR; oasdiff blocks accidental breaking changes on the way to `main`.
- Generated code imports mapped modules in every file, so generated files also disable pyright's unused-import check (header only).
- If Redocly fixes its validator, `no-invalid-media-type-examples` can be turned back on.
