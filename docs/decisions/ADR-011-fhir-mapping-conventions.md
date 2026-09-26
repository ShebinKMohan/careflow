# ADR-011: FHIR R4 mapping conventions

## Status

Accepted, 26 Sep 2026 ([M0-04]).

## Context

`packages/contracts/fhir/mapping.yaml` fixes how every StructuredFacts leaf and every CareFlow output maps to FHIR R4, so the integration issues (M1-08, M1-09, M1-16, M4-*) cannot each invent codes. A few choices were not dictated by the issue or by a standard.

## Decision

- **Verified codes only.** LOINC, SNOMED CT UK and HL7 codes were checked on 26 Sep 2026 against loinc.org, the FHIR R4 spec, the UK Core ACVPU value set, the NHS Clinical Observations guide and the INTEROPen NEWS2 guidance (log in `mapping.md`). The one pattern that could not be confirmed (supplemental oxygen in `Observation.code`) is `verified: false` and listed under Open questions. Where no standard code fits, CareFlow code systems under `https://careflow.example/fhir/CodeSystem/` are used.
- **Priority convention.** `Encounter.priority` maps level 1 to S, 2 to EM, 3 to UR, 4 and 5 to R. It is labelled a CareFlow convention everywhere it appears.
- **Only the nurse's level is written.** The triage-level Observation carries the NurseDecision's final level; a model suggestion is never written as a level.
- **One QuestionnaireResponse per answering source** (patient or parent: `PATIENT_STATED`; nurse: `NURSE_CONFIRMED`), so each response carries a single provenance. The Questionnaire marks nurse-answered items with a CareFlow `audience` extension.
- **Provenance tags on everything written**, including the EHR resources copied into the briefing's document Bundle (tagged `EHR`). A resource that aggregates several sources (Encounter, Composition) lists one tag per source; alert outputs (Flag, Task, Communication) carry the provenance of the alert's evidence.
- **A partial kiosk NEWS2 is written as `preliminary`** with the missing inputs noted, never as a full score.
- **Patient-stated allergies** become `AllergyIntolerance` with `verificationStatus` = `unconfirmed`; EHR allergies are not written back.

## Consequences

- M1-08 and M1-09 implement the table exactly; a new mapping starts as a `mapping.yaml` entry with a verified code or an open question.
- UK SNOMED CT extension codes are UK-specific; a non-UK hospital maps them in its adapter config.
