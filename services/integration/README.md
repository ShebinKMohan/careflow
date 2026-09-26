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

FHIR model types (`fhir.resources`, R4B models for R4 data) stay inside this package. `tests/test_boundary.py` fails if the API, the intake or the rules import them.

```bash
# Offline: respx replays the recorded persona answers
uv run pytest services/integration
# Against the seeded HAPI server (needs `make up`; skipped when HAPI does not answer)
uv run pytest services/integration -m hapi
# Record a persona's answers again (needs `make up`)
uv run python -m careflow_integration.fhir.record --persona chest-pain
```
