# ADR-009: Shape of the structured-facts contract

## Status

Accepted, 26 Sep 2026 ([M0-02]).

## Context

`packages/contracts/schemas` defines `StructuredFacts`, `UrgencySuggestion`, `Alert` and `NurseDecision` (JSON Schema 2020-12), and Pydantic v2 models are generated from them. The issue fixed most field names; a few shapes were left open, and code generation needed flags beyond the listed ones to give consumers usable types. These names become FHIR mapping keys and Questionnaire linkIds (M0-04), so they are chosen once.

## Decision

- **Every fact has `value`**, including `CodeFact`, whose value is a FHIR-style `Coding {system, code, display}`. Consumers always read `fact.value`.
- **Patient demographics are facts** (`patient.age_years`, `patient.age_months`, `patient.sex`) so the doctor briefing can tag their source; `patient.fhir_patient_id` is a plain identifier. `age_months` is the total age in months.
- **Vitals are specialised `NumberFact`s with a fixed UCUM unit** per path (`/min`, `%`, `Cel`, `mm[Hg]`) and only plausibility bounds (non-negative, SpO2 at most 100). No clinical threshold is in a schema. ACVPU is a `CodeFact` whose code is one of A, C, V, P, U.
- **One current fact per path.** When a nurse enters a vital a device already measured, the nurse's fact replaces the device fact at that path (the device reading survives in events and FHIR Observations). This satisfies M1-01's "use the nurse value".
- **`FactKey` lists every leaf fact path**; list-valued paths name the list (`symptoms`, `ehr_context.allergies`). `NeededFact.key`, `Turn.question_key` and `Alert.missing_inputs` all use `FactKey`, so missing inputs read `vitals.respiratory_rate`, not the bare `respiratory_rate` in the issue's example.
- **`NurseDecision.suggestion_id` is required but nullable**: null only when no suggestion was available (the model failed), so the nurse can still set a level. This fails safe.
- **Cross-field rules live in the schema and in code.** The provenance-source pairing and "OVERRIDE needs a reason" are `if`/`then` in the schemas; Pydantic does not enforce `if`/`then`, so `careflow_contracts.validators` re-checks them together with span offsets, unique fact ids and evidence references.
- **Alert text is data, not code.** `alert_templates.yaml` holds the per-rule `en`/`ar` line and `required_action` key; `make contracts` compiles it to `generated/alert_templates.py`, so the pure rules engine never reads a file.
- **Code generation** adds `--collapse-root-models --use-type-alias --strict-nullable` (plain `str` ids and a discriminated `fact.source.kind` instead of RootModel wrappers; defaulted lists are not nullable) and formats with ruff. Generated files carry a DO-NOT-EDIT header that disables pyright's `reportIncompatibleVariableOverride` for them only (a narrowed field such as `unit: Literal["Cel"]` over `str` is intended and validated by Pydantic). Ruff skips `generated/`; CI regenerates and fails on any diff.

## Consequences

- Consumers get typed, discriminated models and never redefine them; a new field goes into a schema first and is regenerated.
- Unknown fields are rejected by both jsonschema (`additionalProperties`/`unevaluatedProperties: false`) and Pydantic (`extra='forbid'`), which also catches invented fields in LLM output.
- `datamodel-codegen`'s `--use-type-alias` is marked experimental; it is pinned (0.83.0) and the determinism check in CI catches any change in its output.
