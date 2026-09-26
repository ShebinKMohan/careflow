# ADR-016: Rules engine core, NEWS2 and SEPSIS_NEWS2

## Status

Accepted, 27 Sep 2026 ([M1-01]).

## Context

Safety rule 1 requires deterministic code that catches danger signs before any urgency suggestion, and that no model can override. The contract (M0-02) already defines `StructuredFacts`, `Alert`, `NeededFact`, `FactKey`, the alert templates, and the `sepsis_partial` example. The issue leaves several details open: the output models, how "not applicable under 16" is represented, rounding, which facts count as evidence, alert ids, and how a nurse value replaces a device value.

## Decision

- **Outputs:** `RulesResult` (`ruleset_version`, `alerts`, `needed_facts`, `news2`, `news2_not_applicable`) and `News2Result` are frozen Pydantic models in `careflow_rules`, because they are engine outputs, not API bodies. Every input and alert type comes from `careflow_contracts`; nothing is redefined locally.
- **Rounding:** `Decimal(repr(value))`, half up. Temperature is rounded to 1 decimal and everything else to a whole number, so 38.05 °C scores as 38.1 and SBP 90.5 as 91.
- **Missing and not applicable:** only the inputs present are scored, and `partial` is set when any of the 7 is missing. An `unknown` oxygen answer counts as missing. Missing inputs are listed in the order RR, SpO2, SBP, pulse, ACVPU, temperature, air/oxygen; at the kiosk this gives the contract example's RR, SBP, ACVPU, oxygen. Under 16, `score_news2` returns `None` and `RulesResult.news2_not_applicable` says "NEWS2 not applicable under 16", so no SEPSIS_NEWS2 is raised (children: NICE NG143, M1-02). An unknown age is scored as an adult, which errs towards alerting.
- **Nurse over device:** `StructuredFacts` holds one current fact per vitals path (contract). `merge_vitals(current, update)` is the pure merge a caller (M1-12) applies when readings arrive. A nurse fact always wins, even over a newer device reading; otherwise the newer fact wins.
- **SEPSIS_NEWS2:** infection is suspected from `infection.suspected`, `fever_reported` or `rigors` = yes; from a measured temperature (device or nurse provenance; a temperature the patient states is not a measurement) of 38.1 or above, or 36.0 or below; or from a patient-stated symptom in `INFECTION_SYMPTOMS`. With a suspected infection, the alert fires at NEWS2 5 or more. A partial NEWS2 marks the alert partial, lists `missing_inputs` and adds the fixed qualifier "partial NEWS2 (kiosk vitals only)". Every missing NEWS2 input becomes a nurse `NeededFact` (`news2_incomplete`) whenever infection is suspected, even below 5, so the nurse completes the score.
- **Evidence:** the infection triggers come first (suspected, fever, rigors, measured temperature, symptoms), then each NEWS2 input with a subscore above 0, without duplicates. This is a superset of the contract example's four facts.
- **Alert ids:** `alr_` plus the first 24 hex characters of sha256(`session_id|rule_id`). The id is stable across re-evaluations of the same session and rule, and fits the 64-character `Identifier`. `raised_at` is the `now` passed in. SEPSIS_NEWS2 sets no `deadline_at`.
- **Engine:** a `Rule` has an `id` and `evaluate(facts, ctx)`; `RuleContext` carries `now`, the version and the NEWS2 result. `evaluate` rejects a naive `now`, runs the rules in order, keeps the first alert per rule id, sorts critical before high (stable), and dedupes needed facts by key and audience.
- **Purity:** an AST test rejects imports of network, database, provider, file-system, process, clock and randomness modules, calls to `open`/`eval`/`exec`, and clock calls such as `.now()`.
- **Test support:** `careflow_rules.testing` builds synthetic facts for M1-02 and the M1-18 scenario suites. `evaluate()` never uses it.

## Consequences

- M1-02 adds its rules to `DEFAULT_RULESET` and bumps `RULESET_VERSION` with a CHANGELOG entry.
- M1-12 calls `merge_vitals()` when nurse vitals arrive, then `evaluate()`, and shows `news2` as partial whenever `partial` is true.
- `/v1/admin/status` now reports `ruleset_version` `2026.1.0`, because the API finds `careflow_rules` in the workspace; M1-12 makes it a declared dependency.
