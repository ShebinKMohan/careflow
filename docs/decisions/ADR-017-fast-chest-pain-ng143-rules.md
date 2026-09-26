# ADR-017: FAST, chest-pain and NICE NG143 rules, and the scenario suite

## Status

Accepted, 27 Sep 2026 ([M1-02]).

## Context

M1-02 adds three rule families to the engine from ADR-016. NICE NG143's traffic-light table sets age-dependent thresholds, while the facts may give an age only in whole years, or no age at all. The contract (M0-02) had no fact for the amber sign "not responding normally to social cues / no smile". The issue asks for every item to be verified on nice.org.uk. The fetch tool got HTTP 403 there, but the in-app browser read Table 2 (published 7 Nov 2019, last updated 26 Nov 2021).

## Decision

- **Rule order:** FAST_STROKE, CHEST_PAIN_ECG_10MIN, NG143 (red and amber), SEPSIS_NEWS2. `RULESET_VERSION` becomes 2026.2.0. `RuleContext` gains `arrived_at`, and `evaluate(..., arrived_at=...)` rejects a naive time. All rules build alerts through one `raise_alert()`, which renders the contract template text, sets a stable id and dedupes evidence.
- **FAST:** any sign yes raises the alert. `fast.last_known_well` is evidence when known. When it is missing, or its text says unknown or unsure, the patient is asked for it (reason `fast_screen`). A stated symptom in the FAST screen list asks the FAST questions that are still unanswered.
- **Chest pain:** `deadline_at` = `arrived_at` + 10 min, falling back to the chest-pain fact's `recorded_at` + 10 min. Yes-answers to radiating pain, breathlessness and sweating are evidence. Unanswered chest-pain questions go to the patient (`chest_pain_screen`).
- **NG143 (children under 5):**
  - Red and amber are separate alerts and can fire together. Suppressing amber when red fires would let an extra finding remove an alert, which the "danger only adds alerts" property forbids.
  - Age comes from `patient.age_months` when present. Otherwise `patient.age_years` gives the 12 possible months, and each threshold uses the end of that range that alerts sooner: the youngest month for the infant fever rules, the oldest for the respiratory and heart-rate limits. The exact month from the EHR birth date avoids this over-alerting.
  - Without any age, only the child-screen answers apply. These questions are only asked of children, so a "hard to wake" answer still alerts; age-dependent vital thresholds need an age.
  - A parent-reported seizure is red. NICE lists status epilepticus and focal seizures, and a kiosk answer cannot tell them from other fits. SpO2 of 95% or less is amber in air and also on supplemental oxygen, where it means hypoxia despite oxygen.
  - Vital thresholds use the half-up rounding of ADR-016 and accept any provenance.
  - A febrile child (reported fever, or a temperature of 38.0 °C or more) or an unwell child (a complaint, a symptom or any screen answer) gets the six parent-screen questions that are still unanswered (`ng143_parent_screen`).
- **Contract:** new fact `child_amber.not_responding_normally`, with a schema entry, a `FactKey` value, a Questionnaire item and a FHIR mapping row. The red question for `child_danger.no_response_to_social_cues` is reworded to "not responding at all", because its old text ("no smile") described the amber sign. oasdiff reports no breaking change.
- **Scenario suite:** `eval/scenarios/rules/*.yaml`, one scenario per file, read with `yaml.safe_load` and validated by `_schema.json`. The schema requires string timestamps and string or number values, so an unquoted YAML `yes` (a boolean) fails validation instead of silently changing meaning. `covers` names the feature each scenario exists for; the tests require every NG143 red and amber feature to be covered, and alerts to match `expect_alerts` exactly. `careflow_rules.testing.facts_from_entries` turns scenario entries into `StructuredFacts`, taking each path's type from the contract models.

## Consequences

- M1-07 (conversation) turns needed facts into questions by `reason`: `fast_screen`, `chest_pain_screen`, `ng143_parent_screen` for the patient, and `news2_incomplete` for the nurse.
- M1-18 reuses the scenario files; M2-04 builds the child flow on these rules, and may add `CHILD_HARD_TO_WAKE` with the `parent_unsure` qualifier, which exist in the contract but are unused here.
- Using the youngest possible month for infant fever over-alerts an infant whose age is known only in years (for example a 10-month-old at 38.2 °C gets red). This is deliberate: a missed red flag costs more than an extra nurse review.
