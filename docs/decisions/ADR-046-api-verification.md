# ADR-046: Verify the API against its contract and a real, isolated stack

Status: accepted, 27 September 2026 (SHE-303 [M1-12]; SHE-315 [M1-19] builds its journeys on it)

## Context

The M1-12 routes and intake runtime were merged (ADR-031) without their acceptance evidence: no
contract tests, no full-flow run against Postgres, Valkey and HAPI, and no proof of the decision,
task, rate-limit, concurrency and isolation guarantees. Four things stood in the way:

- The owner's dev stack (Compose project `careflow`, ports 5432/6379/8080) was the only stack,
  and tests shared its event stream (ADR-043). Integration tests must not touch it.
- The FHIR endpoint had two sources: `FHIR_BASE_URL` for the health check, the briefing and the
  feels-worse Flag, and the hospital adapter file (port 8080) for the check-in lookup, the
  write-back and the task completion. A stack pointed elsewhere still wrote to 8080.
- Free-text answers need the extraction model; a deterministic test cannot depend on a live one.
- The LangGraph checkpoint tables come from `careflow-api setup-checkpoints`, not Alembic, so
  the test database never had them.

## Decision

- **Isolated test stack.** `scripts/stack.py` runs a second Compose project, `careflow-test`,
  on the CONVENTIONS ports +10000 (Postgres 15432, Valkey 16379, HAPI 18080), seeds the seven
  synthetic personas into its HAPI, and writes its connection URLs to `.run/stacks/<stack>.env`
  (mode 0600, never printed); `stack.py run -- <cmd>` runs a command against it. Compose's
  working-dir label decides ownership: `up` refuses a stack another checkout runs, `down` stops
  only its own, the dev stack's volumes are never deleted, and `up` refuses to pass the 6 GiB
  Docker budget. `make test-stack`, `make test-integration`, `make test-stack-down`.
- **One FHIR endpoint.** `load_adapter` takes the endpoint from `base_url`, else `FHIR_BASE_URL`,
  else the adapter file; the API resolves it once (`careflow_api.hospital`) and every reader and
  writer uses that adapter.
- **Recorded providers.** `CAREFLOW_PROVIDER_MODE=record|replay` (cassette `CAREFLOW_CASSETTE`)
  wraps every provider in the one `ReplayProvider`; replay never builds the real provider and a
  miss raises `CassetteMiss`. A replayed extractor reports provider `replay`, as do the admin
  provider rows. `CAREFLOW_INTAKE_CONFIG` selects a deterministic intake config
  (`llm_phrasing: false`, `briefing_summary: false`).
- **Contract tests.** `services/api/tests/contract/test_contract.py` (schemathesis 4.28.0,
  pinned): the app serves exactly the contract's operations; schemathesis generates requests
  from `packages/contracts/openapi.yaml` (not FastAPI's schema) for every operation and sends them
  to the real app with its lifespan, each with a token for the operation's first
  `x-careflow-roles` role, bound to the path's session. All default checks run (server errors,
  documented statuses, content types, response schemas, negative-data rejection, positive-data
  acceptance, auth enforcement, Allow headers). Providers replay an empty cassette, so no model
  is ever called. 25 examples per operation in CI (`CAREFLOW_CONTRACT_EXAMPLES` raises it).
- **What the contract tests found**, fixed on the side the contract says: 400 for an unparseable
  body is documented on every operation with a body; 429 on the five rate-limited routes; 404 on
  the companion routes for an unknown session; `ws-ticket` requires `session_id` for the session
  channel (if/then; the API already answered 422, so the oasdiff entry is reviewed); a 405 lists
  every method of its path in `Allow`; `/v1/auth/me` omits `display_name` rather than sending
  null; the dev vitals simulator rejects a null body or scenario; the test database gets the
  checkpoint tables, as `make migrate` does.
- **Flow tests.** `services/api/tests/test_intake_flow.py` runs the M1-12 flow on the real app
  and the isolated stack's Postgres, Valkey and HAPI: keypad check-in of a seeded persona, the
  intake, device and nurse vitals, the decision, the token, the ECG task in the facts and in
  HAPI, and the negative cases. Its free-text answers replay replies recorded once from the local
  Qwen3.5-9B model through `scripts/with_llm.sh` (that run is the live-model evidence). It needs
  HAPI, so CI (Postgres and Valkey only) skips it with a reason; `make test-integration` runs it.

## Consequences

- Integration tests never touch the owner's services, and a fresh checkout cannot stop them.
- The contract is now enforced in CI for every operation, and any route added without its
  contract entry (or the reverse) fails the inventory test.
- The flow test's recording pins the extraction prompt: a prompt or template change makes the
  replay miss, and the cassette has to be recorded again with the local model (documented in the
  test's docstring). That is the intended regression signal, not flakiness.
- Two stacks with warm HAPIs use about 2.6 GiB together (measured), inside the 6 GiB budget; the
  test stack's volumes persist between runs until `stack.py down --volumes`.
