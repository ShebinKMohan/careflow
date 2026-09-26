# ADR-023: The FHIR R4 read client, the hospital adapter and the EHR degraded mode

## Status

Accepted, 27 Sep 2026 ([M1-08]).

## Context

The intake's `fetch_ehr_context` node (M1-05) reads the patient's record through an `EhrReader` port, which until now was a stub. M1-08 implements the port over FHIR R4 against the seeded HAPI server (M0-05). It has to:

- keep the intake running when the EHR is slow, down or wrong;
- never show an empty record as if it were complete.

The issue sketches the port as `fetch_context(identifier, clinic_id) -> EhrContext(status, patient_ref, age_years, sex, facts, fetched_at, errors)`, with fact keys `ehr_allergy`, `ehr_condition`, `ehr_medication` and `ehr_appointment`. The contracts from M0-02 already define `EhrContext` as the `ehr_context` block of `StructuredFacts`, with the keys `ehr_context.allergies`, `ehr_context.conditions` and `ehr_context.medications`. The M0-04 mapping table says appointments are check-in context, not facts.

## Decision

### FHIR models: `fhir.resources` 8.3.0, R4B

- **The version question.** The pinned `fhir.resources` 8.3.0 (Pydantic v2) ships R5 as its default package, plus `R4B` and `STU3`. It has no plain R4 package.
- **Why R4B works.** R4B (4.3.0) is a small release on top of R4 (4.0.1). Every element read here parses the same in both: `code`, `clinicalStatus`, `criticality`, `status`, `medicationCodeableConcept`, `start`, `birthDate`, `gender` and `meta.versionId`. The M0-04 examples already parse with R4B. So the R4 resources from HAPI are parsed with the R4B models.
- **Unparseable resources.** A resource that does not validate is skipped and logged (`fhir.resource_skipped`, with its type and no patient data). It never raises.
- **Where FHIR types live.** Only in `careflow_integration`. `test_boundary.py` scans `careflow_api`, `careflow_intake` and `careflow_rules` and fails on any `fhir` import. The rest of CareFlow sees contract models only, which keeps R5/R6 possible later.

### Contract

- **`EhrFetch`** (new, `ehr-fetch.schema.json`) is the port's result. It wraps the existing `EhrContext` instead of redefining it:

  | Field | Meaning |
  |---|---|
  | `status` | `ok`, `partial`, `unavailable`, `not_found` or `ambiguous` |
  | `patient_ref` | `Patient/<id>` when exactly one patient matched |
  | `patient` | the `StructuredFacts.patient` demographics: FHIR id, `age_years`, `age_months` under 5 years, `sex` |
  | `context` | `EhrContext`: allergies, conditions, medications |
  | `appointments` | `EhrAppointment` list: id, status, start, description, FHIR source. Context for check-in, not facts |
  | `fetched_at` | when the read happened |
  | `errors` | short reasons, never patient data |

- **`AllergyFact`** is a coded fact plus `criticality` (`low`, `high` or `unable-to-assess`). `EhrContext.allergies` now holds `AllergyFact`s. It is a sibling of `CodeFact`, not an extension, because `CodeFact` closes itself with `unevaluatedProperties: false`.
- **`FhirSource.version_id`** is new and optional. It holds the resource's `meta.versionId`, so a fact says which version of the record it came from.
- **Fact ids.** `stable_fact_id` moved from the intake to `careflow_contracts.ids`, so the intake and the mapper share it. An EHR fact's id is derived from (resource type, id, version, part), so re-reading an unchanged record gives the same ids and the facts merge instead of duplicating.
- **oasdiff.** Every change is additive (a new schema, an optional field, a new `$def`), so no new ignore lines are needed.

### The intake port

- **`Identifier.identify(state)`** returns a `PatientIdentifier` (system and value), or `None` for an anonymous patient.
- **`EhrReader.fetch_context(identifier, clinic_id)`** returns an `EhrFetch`. It reports EHR trouble as a status and does not raise. The old `EhrUnavailable` exception is gone.
- **`fetch_ehr_context`** behaves like this:

  | Result | What the node does |
  |---|---|
  | `ok` | uses the context and the demographics (age and sex become facts) |
  | `partial` | uses them the same way |
  | `unavailable`, `not_found`, `ambiguous` | keeps no context and sets the `ehr_unavailable` flag, so the allergy question is asked fresh |
  | no identifier | the same as `not_found` |
  | the reader crashes | the same as `unavailable` |

  The nurse review payload carries `ehr_status` and `ehr_errors`, so a partial or missing record is never shown as a complete empty one.
- **Checkpoint gotcha.** `ehr_status` is a top-level `StrEnum` channel. LangGraph's Postgres saver stores `str` values inline, so after a restart the channel holds a plain `str`, not an `EhrStatus`. `review_payload` reads it through `EhrStatus(...)`. The M1-05 restart test caught this. Any future top-level enum channel needs the same care.

### The client (`FhirClient`)

- **Transport.** `httpx.AsyncClient` with `Accept: application/fhir+json` and `Timeout(5.0, connect=2.0)`. The adapter can override the timeouts.
- **Paging.** `search` asks for `_count=100` and follows `Bundle.link[relation=next]` for at most 5 pages. A next link to another scheme, host or port is refused, so a server cannot send the client elsewhere.
- **Retries.** Two retries, after 0.2 s and 0.8 s, on connection errors, timeouts and HTTP 429, 502, 503 and 504. No other status is retried.
- **Auth.** An `auth` hook stays `None`; SMART Backend Services comes in M4-04.

### The reader (`FhirEhrReader`)

1. **Find the patient.** `Patient?identifier=<system>|<value>`. One match continues. None is `not_found` and several are `ambiguous`, with the count as the error.
2. **Read the context.** These searches run together (`asyncio.gather`):
   - `AllergyIntolerance` and `Condition` with `clinical-status=active`;
   - each medication type in the adapter with `status=active`;
   - `Appointment` with `date=ge<today>`, where today is the UTC date of the reader's clock.
3. **Set the status.**
   - A failed search is recorded as `"<type>: <reason>"`, and the other searches still count.
   - Some searches failing is `partial`. All of them failing is `unavailable`.
   - A failed patient search is `unavailable`.
   - Any other exception is a bug and is raised.
4. **Budget.** The whole read has 8 s (`asyncio.wait_for`). Past that it is `unavailable` ("no answer within 8 s").
5. **Circuit breaker.** It lives in memory, one per reader.
   - After 3 `unavailable` results in a row, it answers `unavailable` at once ("circuit open after repeated EHR failures") for 30 s, without a request.
   - After 30 s, calls go through again. A success closes the breaker. A failure opens it again at once, because the failures in a row are not reset.
   - `not_found` and `ambiguous` count as successes, because the EHR answered.

### The hospital adapter (`config/hospitals/demo_hapi.yaml`)

| Field | Value for `demo_hapi` |
|---|---|
| `fhir_base_url` | `http://localhost:8080/fhir` |
| `identifier_systems` | `keypad`: the kiosk id (`https://careflow.example/fhir/sid/keypad-id`, the personas); `mr`: the Synthea MR number (`http://hospital.smarthealthit.org`, checked on the loaded data) |
| `display_paths` | `text`, then `coding[0].display`, on the concept |
| `condition_categories` | `problem-list-item`, `encounter-diagnosis` |
| `medication_types` | `MedicationStatement`, `MedicationRequest` |
| `appointment_statuses` | `booked`, `arrived` |
| `timeouts` | connect 2 s, read 5 s, total 8 s |

### The mapper

- **Codes.** A concept maps to its first coding with a system and a code of at most 64 characters, with the display the adapter prefers.
  - Text with no usable coding is kept as system `urn:careflow:uncoded`, code `uncoded`, with the text as display. The nurse still sees what the EHR wrote.
  - A concept with neither a coding nor text is skipped.
- **Condition categories.** A Condition is kept when its category is in the adapter list, or when it has no category (the personas have none).
- **Medications.** Only `medicationCodeableConcept` is read; a `medicationReference` is not resolved. On the loaded data all 632 active MedicationRequests are coded concepts, so nothing is lost.
- **Demographics.** Age comes from `birthDate` at the read date. The exact age in months is added under 5 years, for NG143. Sex comes from `gender`.
- **Provenance.** Every fact has provenance `EHR` and a `fhir` source (resource type, id, version).

### Recorded fixtures

`python -m careflow_integration.fhir.record --persona <id> [--today YYYY-MM-DD]` saves each search's resources to `services/integration/tests/fixtures/fhir/<persona>/<Type>.json`. The 7 personas were recorded on 26 Sep 2026. The offline tests replay them with respx.

## Consequences

- **M1-09** adds writes (POST/PUT) to `FhirClient` and reuses the adapter and the source ids.
- **M1-12** composes the reader into the API with `build_real_ports(ehr=FhirEhrReader(...))`, plus an `Identifier` that turns the kiosk keypad id into the adapter's keypad system.
- **Nurse and doctor views** (M1-13, M1-15) show "EHR unavailable at check-in" from `flags.ehr_unavailable` and `ehr_status`, and mark a `partial` record as incomplete.
- **Per-process breaker.** Several API workers each keep their own breaker. That is acceptable for the prototype; a shared breaker would need Valkey.
- **Synthea noise.** Synthea stores social findings as active `encounter-diagnosis` Conditions, for example "Full-time employment (finding)" and "Received higher education (finding)". They reach `ehr_context.conditions`. The briefing work can drop them with a per-hospital exclusion list if they get in the way. The personas are not affected.
- **M4-04** fills the `auth` hook with SMART Backend Services.
