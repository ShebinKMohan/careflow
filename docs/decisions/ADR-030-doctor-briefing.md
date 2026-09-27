# ADR-030: The doctor briefing

## Status

Accepted, 27 Sep 2026 ([M1-16]).

## Context

The doctor briefing is the product's main output. Three safety rules apply:

- **Rule 4:** every briefing fact carries its provenance.
- **Rule 2:** no model output reaches a patient as medical advice, and a summary must not add information.
- **Rule 3:** the nurse decides the level, so a model's suggestion is never shown without the nurse's level.

The contract's `Briefing` (M0-03) was a stub marked "extended by M1-16". The issue's per-provenance source fields (`char_start`/`char_end`, `measured_at`, `nurse_id`/`at`) differ from the contract's `SourceRef`, and the contract wins (CONVENTIONS).

## Decision

### Contract (additive)

- **`Briefing`** gains:
  - `sections`: exactly 7;
  - `summary` (`BriefingSummary` or null);
  - `summary_status`: `ok`, `dropped` or `disabled`;
  - `triage`: `suggested` (or null) beside `confirmed`, plus `override_reason` (or null);
  - `versions`: `ruleset`, `model` and `prompt`.
- **`BriefingSection`** gains `id` (`BriefingSectionId`, in the fixed order) and optional `notes`.
- **`BriefingItem`** gains `recorded_at` (required) and optional `source_missing`.
- **`BriefingNote`** (`code`, `text`, optional `count`): a fixed line about the briefing itself, not about the patient, so it has no provenance.
  - Codes: `extraction_incomplete`, `intake_stopped_early`, `ehr_unavailable`, `ehr_partial`, `facts_dropped`, `suggestion_unavailable`, `news2_not_applicable` and `none_recorded`.
  - Data-quality warnings are notes. So is an empty section, which is never shown as if it were complete.
- **`SourceRef` is unchanged.** The issue's fields map onto the contract:
  - character offsets are the transcript span's `start`/`end`;
  - a device reading is `reading_id` plus the item's `recorded_at`;
  - a nurse is `staff_user_id` plus `recorded_at`.
- **The patient's words** are the transcript span's `quote`. The turn's full text is not copied into the briefing, because transcripts are kept for the session only (safety rule 5).
- **`FactKey` gains `tasks.ecg_done_at`**, with a `Tasks` group in `StructuredFacts`:
  - it is a `NURSE_CONFIRMED` fact that M1-12's task-complete route sets;
  - it is excluded from `extractable_keys()`, so it never comes from the patient's words;
  - `mapping.yaml` maps it to the `ecg_within_10_min` Task.
- **The route example** is a trimmed real briefing (chest-pain persona) with its recorded summary.

### The builder (`careflow_intake.briefing.builder`)

- **Built only after the nurse's decision** (`BriefingNotReady` otherwise).
- **Item text comes from fixed templates over structured values**, never from a model.
  - Times are relative to arrival (e.g. "ECG done: 7 min after arrival.").
  - A patient-stated item ends with `Patient: "<quote>"`.
- **Sections, in order:**
  1. **Alerts and triage:**
     - alerts, critical first. An alert the nurse acknowledged (`acknowledged_alert_ids`) is `[Nurse]`; otherwise it carries its first evidence fact's source;
     - the triage line (`[Nurse]`);
     - the ECG-done fact (`[Nurse]`).
  2. **Presenting complaint:** age and sex (EHR), then the complaint facts.
  3. **Danger-screen answers:** FAST, chest pain, infection, and the NICE NG143 red and amber signs.
  4. **Vitals and NEWS2:**
     - every current vital;
     - NEWS2 from the rules engine, sourced to its latest input: the nurse's entry once the nurse completed it, else the device reading;
     - a partial NEWS2 reads "Partial NEWS2 n from k of 7 parameters … Not a full score.";
     - under 16 there is a note instead.
  5. **Record (EHR):** EHR allergies, conditions and medications, and the patient's allergy answer.
  6. **Model output:**
     - the suggestion, its three reasons and the model-inferred complaint category;
     - every one of these ends with "Nurse's level: KTAS n.";
     - with no suggestion there is a note instead.
  7. **Data quality:** notes only.
- **Fail closed:** an alert whose evidence is missing is still shown, marked `source_missing`.

### The check (`check_briefing`)

- An item's source is valid when:
  - its kind pairs with its provenance;
  - an item that names a fact matches that fact in the session exactly (tag and source);
  - any other item cites a source the session holds (a fact's, the deciding nurse's, or the suggesting model's);
  - a transcript span points into a patient turn and quotes it exactly.
- An invalid item is kept, marked `source_missing` (red on screen and in the document), and logged as `briefing.source_missing` at error level.
- **Result:** the 7 personas have none.

### The summary (`careflow_intake.briefing.summary`)

- **Switch:** `config/intake.yaml` `briefing_summary: true` (false means `disabled`), with `summary_timeout_s: 60`.
- **Provider:** task `summary` from `config/providers.yaml` (the local Qwen3.5-9B by default).
- **Input:** only fact items whose source passed the check, as `<fact_id> [TAG] text`.
- **Prompt (`briefing-summary-v1`):** at most 5 sentences, only rephrase, no diagnosis, no advice, keep the numbers, cite `fact_ids`, one-line JSON.
- **Draft schema:** lenient except for the sentence count, so an over-long reply gets the one retry, with the validation error.
- **Token limit:** `max_tokens` is 1536, because mlx_lm.server's default of 512 tokens cut long replies off; this happened in the first recording.
- **`check_summary` drops the whole summary** when any sentence:
  - cites no fact, or an unknown fact;
  - has a number that is not in the items it cites (compared by value, so "08" equals "8");
  - matches the output filter's diagnosis or advice patterns, or the hedges "likely", "probably", "possible", "suggests", "consistent with", "rule out", "should", "recommend", "advise", "consider" or "diagnos…";
  - names a condition or a dose that its cited items do not contain.
- **Statuses:**
  - a reply that fails validation twice is `dropped`;
  - any provider error is `disabled`: `ProviderUnavailable` (no key), connection refused, a timeout, or a cassette miss.
- **The plain list is always complete.**
- **Not written to the hospital record:** the summary is shown on screen only and is not part of the FHIR document. Model prose stays out of the record, and it adds nothing anyway.

### The FHIR document (`careflow_integration.fhir.writeback.composition`)

- **The Bundle:** `type` document, `identifier` `urn:careflow:briefing|<session_id>`, and a `timestamp`.
- **Entry 1, the Composition:**
  - `status` final, `type` LOINC 54094-8 (from `mapping.yaml`);
  - `subject` and `encounter` (from `fhir_writebacks`), `date`, `author` and a title;
  - one tag per provenance present.
- **Sections:** one per briefing section.
  - **Narrative:** XHTML, one `<li>` per item with its provenance tag, every value `html.escape`d; a failed source adds a red "source missing"; notes are italic lines.
  - **Entries:** the write-back resources behind the section (Flags, Tasks and the triage level; QuestionnaireResponses and the pain score; the vitals and NEWS2 Observations) and the EHR resources its items cite.
- **Entry 2, the author:** a CareFlow `Device` that exists inside the document only; CareFlow still never stores Practitioner or Device resources (ADR-024). Its `version` lists the ruleset, model and prompt versions. It has no provenance tag, since it is not data about the patient.
- **The other entries:** every referenced resource as the server holds it, plus the resources those reference in turn (read by closure, at most 200). EHR copies get the `EHR` tag (ADR-011).
  - The Composition uses absolute references, equal to each entry's `fullUrl`.
  - The copied resources keep their relative references, which resolve against their RESTful `fullUrl`.
- **Posting:** `write_document` posts once to `{base}/Bundle` with `If-None-Exist: identifier=urn:careflow:briefing|<session_id>` (`FhirClient.create`), so a second post stores nothing new.
  - It is `skipped` without an EHR patient or before the triage write-back exists.
  - A FHIR error is `failed` and never raised.

### The API (`careflow_api.briefing`)

- **Alembic 0005, `briefings`:**
  - one row per session, clinic-scoped, with forced row-level security;
  - the app may select, insert and update, never delete;
  - columns: the briefing JSON, `summary_status`, the `source_missing` count, and the document's status, id and error.
- **`BriefingService`** implements the intake's `BriefingBuilder` port: build, check, summarise, store, write the document. The briefing is stored and served even when the document fails.
- **`GET /v1/doctor/sessions/{id}/briefing`:**
  - role `doctor` only, so a nurse gets 403;
  - returns the stored briefing, or builds one on demand from the session's intake state and stores it;
  - 404 when there is no state, the session belongs to another clinic, or the nurse has not confirmed it yet.
  - The state comes through a `StateSource` port. It is `NoStateSource` until M1-12 runs the graph in the API.
- **`careflow-api` now depends on `careflow-intake`.**

### Fixtures and recordings

- **Personas:** `services/intake/tests/fixtures/briefing/<persona>.json` holds the 7 personas as synthetic intake states. `generate.py` builds them with:
  - the real EHR mapper over the seeded persona bundles;
  - the real rules engine, run as the graph runs it;
  - the trained urgency model (`ktas-hgb-ebf31b5e-s42`, a local git-ignored artifact), when it can suggest;
  - a scripted nurse decision.
- **Summaries:** the local model's replies are recorded in `<persona>.cassette.json`, and the outcomes in `<persona>.summary.json`.
- **Hallucinated replies:** 12 hand-written ones are in `hallucinated.json`, with one valid reply.
- **Extraction cassettes were re-recorded.** Adding a `FactKey` changes the JSON schema that `structured()` appends to the extraction prompt, so the M1-06/M1-07 cassettes missed. The 3 scripts and the 3 live cases were recorded again under the same prompt version (the template did not change); every expectation still holds.

## Consequences

- **M1-12:**
  - wires `StateSource` (the graph checkpoint) and `BriefingService` into the graph's `BriefingBuilder` port;
  - sets `tasks.ecg_done_at` from the task-complete route;
  - rebuilds the briefing when a fact arrives after the decision. The stored row is replaced; the FHIR document is not, because the conditional create keeps the first one.
- **M1-17** renders the sections, the notes, `source_missing` in red, the triage block and the summary with its cited items.
- **M1-19** covers the briefing end to end. **M4-04** (SMART launch) opens the document.
- **Adding any `FactKey`** means recording the extraction cassettes again, unless the extraction schema is narrowed to the extractable keys (a follow-up).
