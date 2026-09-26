# careflow-rules changelog

Every threshold or rule change bumps `RULESET_VERSION` (`src/careflow_rules/version.py`) and gets an entry here. Alerts carry the version that raised them.

## 2026.1.0 (M1-01)

- Engine core: `Rule` protocol, versioned `Ruleset`, `evaluate(facts, now) -> RulesResult` (alerts deduped by rule id and sorted by severity; needed facts deduped by key and audience).
- NEWS2 per Royal College of Physicians, NEWS2 (2017), SpO2 Scale 1 only. Temperature is rounded half up to 1 decimal and other values to whole numbers. The score is partial when any of the 7 inputs is missing, and not applicable under 16.
- `SEPSIS_NEWS2` (critical): infection suspected and NEWS2 5 or more. The alert is marked partial (with missing inputs) on a partial NEWS2, and the missing inputs go to the nurse as needed facts.
