# careflow-contracts (`@careflow/contracts`)

Source of truth for CareFlow's interfaces. Every lane imports these types and never redefines them (ADR-002, ADR-009, ADR-010).

| Path | What |
|---|---|
| `openapi.yaml` | OpenAPI 3.1: every REST route (roles in `x-careflow-roles`), RFC 9457 errors, examples Prism serves, and the events under `webhooks`. |
| `schemas/` | JSON Schema 2020-12 for `StructuredFacts`, `UrgencySuggestion`, `Alert`, `NurseDecision` and the fact building blocks (M0-02). |
| `events/` | Standard Webhooks envelope and one data schema per event; [`events/README.md`](events/README.md), [`events/websocket.md`](events/websocket.md). |
| `alert_templates.yaml` | Fixed, non-PHI alert line per rule (`en`, `ar`) and its `required_action` key. |
| `fixtures/` | Valid persona fixtures and invalid cases, `<case>.<schema>.json`; load with `careflow_contracts.testing.load_model`. |
| `codegen/` | `generate.sh` (run by `make contracts`) and the alert-template generator. |

Generated (never edit; CI regenerates and fails on a diff):

- `src/careflow_contracts/generated/`: Pydantic models for `schemas/` and `events/`, plus the compiled alert templates.
- `src/careflow_contracts/api_models.py`: Pydantic models for the OpenAPI components; external `$ref`s import the models above instead of duplicating them.
- `ts/openapi.d.ts`: TypeScript `paths`, `components`, `operations` and `webhooks` (openapi-typescript), exported by `ts/index.ts`.

Commands: `make contracts` (regenerate), `make contracts-check` (Redocly lint, regenerate-and-diff, oasdiff breaking changes against `origin/main`).

Safety rule 4: every fact and every briefing item carries a provenance and a matching source.

Research prototype. Synthetic data only. Not for clinical use.
