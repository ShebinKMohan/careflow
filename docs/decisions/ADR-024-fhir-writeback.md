# ADR-024: The FHIR write-back transaction, its idempotency and its status record

## Status

Accepted, 27 Sep 2026 ([M1-09]).

## Context

When the nurse confirms a triage (`triage.confirmed`), the hospital's own record should show the triage visit without anyone retyping it: the visit, the vitals and NEWS2, the intake answers, the alerts and the ECG deadline.

The write must be:

- **idempotent**, because the event can be delivered twice and a retry must not duplicate anything;
- **valid FHIR R4**;
- **limited to what the nurse confirmed** (safety rule 3).

In places the issue text and the M0-04 mapping table disagree: the Encounter status and class, the role of the Communication, the SpO2 coding and the Provenance. Where they differ, this ADR says which is used and why.

## Decision

### Input: `TriageRecord`

A model in `careflow_integration.fhir.writeback.record` built from contract types:

- the clinic and session;
- the EHR `patient_ref` (optional);
- the arrival time;
- the `NurseDecision`;
- the `StructuredFacts`;
- the alerts;
- a `News2Summary` (total, partial, missing inputs);
- `completed_actions`.

The questionnaire answers are not a separate field. They are the patient-stated and nurse-confirmed facts found at the Questionnaire's linkIds.

**Safety rule 3.** The handler checks that the loaded record holds the decision the event confirms: the same decision id, level and scale. If not, it records `failed` and writes nothing. Only the nurse's final level is ever written, never a model suggestion.

### Resources

| Local id | Resource | Content |
|---|---|---|
| `encounter` | Encounter | Status `triaged`. `class` from the hospital adapter (`encounter_class`: `AMB` by default, `EMER` for `demo_hapi`). `priority`: the ActPriority code by the CareFlow convention, then `triage-level#<scale>-<level>`, plus a text. `period.start` is the arrival time. `reasonCode` is the model-inferred complaint category, tagged `MODEL_INFERRED`. |
| `obs-heart-rate` … `obs-pain-score` | Observation | One per vital present: HR, RR, SpO2 (LOINC 2708-6 plus 59408-5), temperature, the BP panel 85354-9 with its components, ACVPU (SNOMED CT, with the UK Core answer), air or oxygen (in `Observation.code`), and pain. A device reading gets `device`, a nurse value gets `performer` (the nurse), and a patient value gets `performer` (the patient). |
| `obs-news2` | Observation | The NEWS2 total, category `survey`, `derivedFrom` the vitals. A partial score is `preliminary`, with a note naming the missing inputs. |
| `obs-triage-level` | Observation | The nurse's final level, with a `scale` component. |
| `qr-patient`, `qr-nurse` | QuestionnaireResponse | One per answering source. Items are keyed by linkId (the fact path) and nested as the Questionnaire nests them. A choice answer copies the Questionnaire's own `answerOption` coding. EHR, device and model facts are not answers. |
| `flag-<RULE_ID>` | Flag | One per alert: `active`, `clinical`, coded `rule-id#<RULE_ID>` with its fixed display text. |
| `task-ecg_within_10_min` | Task | For an alert with a deadline and a task kind in the mapping: `requested`, `order`, `stat`. Coded `task-kind` with the text "12-lead ECG". `restriction.period.end` is the alert's `deadline_at` and `focus` is its Flag. It is written as `completed` when the action is already in `completed_actions`. |
| `comm-handoff` | Communication | One nurse hand-off built from fixed templates: the level, each alert's display text, vitals, NEWS2, positive screens, pain score and complaint category. It never holds the patient's own words. It is `about` every Flag, and the recipient is the nurse. |
| `provenance` | Provenance | Targets every other resource. One agent per source: device performer, model assembler, nurse author, patient informant. `recorded` is the decision time. |

Every resource carries `meta.tag` provenance-source codes for every fact it holds (safety rule 4). A Flag or Task carries the provenance of its alert's evidence facts. A record whose alert evidence is missing from its facts is refused, because the provenance cannot be known.

### Idempotency and determinism

- **Conditional create for every entry.** `ifNoneExist: identifier=<system>|<value>`:
  - Encounter: `urn:careflow:session|<session_id>`;
  - every other resource: `urn:careflow:fact|<session_id>:<local id>`;
  - the Provenance has no `identifier` in R4, so it matches on `_tag=urn:careflow:writeback|<session_id>`.
- **Deterministic fullUrls.** `urn:uuid:` plus a UUIDv5 of that match key. References inside the Bundle use these values.
- **No clock.** Nothing in the builders reads the clock: times come from the record. The client sends the Bundle as canonical JSON (sorted keys, no spaces), so one record always gives the same bytes.
- **Checked on HAPI.** The first post answers `201 Created` for all 16 chest-pain resources. The second answers `200 OK` with the same ids. A search by each identifier finds exactly one resource.
- **Limit.** A second nurse decision for the same session is not written over the first, because the conditional create keeps what exists. Updating it would need conditional updates. No M1 flow re-triages a written session.

### References

- **Staff and devices are logical references.** `Reference.identifier` uses the systems `urn:careflow:staff` and `urn:careflow:device`, and the model uses `urn:careflow:model`.
- **CareFlow creates no Practitioner or Device resources** in the hospital's record. HAPI checks referential integrity only for literal references.
- **The patient is the EHR Patient.** A record without a `patient_ref` (not identified at check-in) is `skipped`.

### Posting, retries and the status record

- **Sending.** `FhirClient.transaction` POSTs the Bundle to the base URL once, with `Content-Type: application/fhir+json`.
- **Retries.** The handler retries 3 times, after 1 s, 2 s and 4 s, on connection errors, timeouts and 429/502/503/504. Any other status fails at once.
- **Reading the answer.** The transaction-response is read entry by entry: local id, type, id, version, and whether it was created.
- **Outcome.** The status is `ok`, `failed` or `skipped`, with the number of attempts and a short error that never holds patient data. An EHR problem never raises.
- **Tables.** Alembic `0002` adds `fhir_writeback_status` (one row per session) and `fhir_writebacks` (one row per resource). Both are clinic-scoped with RLS, and the app role cannot delete from them.
- **Stores.** `careflow_api.writeback_store.SqlWritebackStore` implements the `WritebackStore` port, and `MemoryWritebackStore` serves tests. `careflow-api` now depends on `careflow-integration`; the API still never imports `fhir` (the M1-08 boundary test).
- **Event.** `fhir.write.completed` {clinic_id, session_id, bundle_id, resource_count} is published after a successful write. It is internal: `careflow_contracts.events.INTERNAL_DATA_MODELS` lists it, and it is not a webhook `EventType`.
- **`complete_task(clinic_id, session_id, task_id)`.** It completes only a Task this session wrote, as a versioned update (`If-Match: W/"<version>"`) that keeps the Task's tags. An already completed Task is left as it is.

### Codes

- **From the mapping table.** Codes come from `mapping.yaml`. The few the table gives only in notes are constants next to that note: 59408-5, 85354-9, the ACVPU answers, the flag category and the priority convention.
- **Verified on 27 Sep 2026:** v3 ActCode `EMER` "emergency" and Encounter status `triaged`.
- **New in the mapping table:**
  - the `triage-level#<scale>-<level>` priority coding;
  - the Encounter status and class rule;
  - the hand-off Communication;
  - the Provenance tag match;
  - a "Write-back transaction" section in `mapping.md`.

### Validation

- **Before sending.** Each resource, and then the Bundle, is validated with the R4B models. A failure is a builder bug: the outcome is `failed` ("bundle not built") and nothing is sent.
- **On HAPI.** Every resource written for the chest-pain, sepsis and routine records passes `$validate` with no `error` or `fatal` issue. The only warnings are the unknown CareFlow code systems and the dom-6 narrative best practice.

## Consequences

- **M1-11** registers `on_triage_confirmed` as consumer group `fhir-writeback` and gives the handler the real event publisher.
- **M1-12:**
  - builds the `TriageRecord` from the intake state: NEWS2 through `careflow_rules`, and `completed_actions` from the task route, since the contract has no ECG-done fact;
  - composes `WritebackHandler` with `SqlWritebackStore`;
  - routes `POST /v1/nurse/sessions/{id}/tasks/{task_id}/complete` to `complete_task`.
- **M1-16** writes the Composition with the same identifier scheme.
- **M1-17** shows `SqlWritebackStore.status` on the admin page.
- **M5-01** records `fhir.write.completed` in the audit log.
- **M4-02** adds UK Core profiles.
- **Test data.** The `hapi` tests write under a fresh session id and delete what they wrote. The evidence runs did the same.
