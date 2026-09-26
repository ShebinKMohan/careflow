# careflow-integration

CareFlow hospital integration: FHIR R4, webhooks, CDS Hooks, SMART, HL7.

Research prototype. Synthetic data only. Not for clinical use. Never point this client at a real EHR.

## FHIR R4 read (M1-08, ADR-023)

- **`fhir/client.py`**, `FhirClient`: async search and read. Search pages with `_count=100` for at most 5 pages and never follows a next link off the server. Timeouts are 5 s read and 2 s connect. It retries twice (0.2 s, 0.8 s) on connection errors, timeouts and HTTP 429/502/503/504, and never on any other status.
- **`fhir/reader.py`**, `FhirEhrReader.fetch_context(identifier, clinic_id) -> EhrFetch`: the intake's `EhrReader` port.
  - It finds the patient by identifier, then reads active allergies, conditions and medications, plus appointments from today on.
  - The status is `ok`, `partial`, `unavailable`, `not_found` or `ambiguous`. It never raises for an EHR problem.
  - The whole read has an 8 s budget.
  - A circuit breaker answers `unavailable` at once for 30 s after 3 failures in a row.
- **`adapters/`**: `loader.py` reads `config/hospitals/<id>.yaml` (base URL, identifier systems, display preferences, condition categories, medication types, appointment statuses, timeouts). `mapper.py` turns resources into contract facts with provenance `EHR` and a FHIR source (type, id, version).
- **`fhir/record.py`**: records a seeded persona's HAPI answers for the offline tests.

## FHIR write-back (M1-09, ADR-024)

When the nurse confirms a triage, `fhir/writeback/` writes it to the hospital's FHIR server as one transaction Bundle.

- **Contents.** The Encounter, the vital-sign, NEWS2 and triage-level Observations, the QuestionnaireResponse, a Flag per alert, the ECG Task, a nurse hand-off Communication and a Provenance.
- **`builders.py`.** Pure functions from a `TriageRecord` to resources. Codes come from `packages/contracts/fhir/mapping.yaml`, and every resource is checked against the R4B models.
- **`bundle.py`.** `build_transaction` makes every entry a conditional create (`ifNoneExist` on its `urn:careflow:*` identifier, the Provenance on its tag), with UUIDv5 fullUrls. The same record builds the same bytes, and posting it again creates nothing new.
- **`handler.py`.** `WritebackHandler.on_triage_confirmed` checks that the record is the confirmed decision (safety rule 3). It posts the Bundle, retries 3 times with backoff, records `ok`, `failed` or `skipped` through the `WritebackStore` port (Postgres in `careflow_api.writeback_store`), and publishes the internal `fhir.write.completed` event. `complete_task` completes a written Task with a versioned update.
- **`validate.py`.** Runs the server's `$validate`, for tests and evidence.

Test records and golden files live in `tests/fixtures/writeback/`. `generate.py` rebuilds the records; `CAREFLOW_UPDATE_GOLDEN=1` rewrites the golden files for review.

FHIR model types (`fhir.resources`, R4B models for R4 data) stay inside this package. `tests/test_boundary.py` fails if the API, the intake or the rules import them.

```bash
# Offline: respx replays the recorded persona answers
uv run pytest services/integration
# Against the seeded HAPI server (needs `make up`; skipped when HAPI does not answer). The
# write-back tests write under a fresh session id and delete what they wrote.
uv run pytest services/integration -m hapi
# Record a persona's answers again (needs `make up`)
uv run python -m careflow_integration.fhir.record --persona chest-pain
```
