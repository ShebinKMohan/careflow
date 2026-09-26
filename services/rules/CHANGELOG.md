# careflow-rules changelog

Every threshold or rule change bumps `RULESET_VERSION` (`src/careflow_rules/version.py`) and gets an entry here. Alerts carry the version that raised them.

## 2026.2.0 (M1-02)

- `FAST_STROKE` (critical): any FAST sign is yes. The alert carries `fast.last_known_well` as evidence when it is known; when it is missing or recorded as unknown, the patient is asked for it. Stroke-like symptoms (weakness, numbness, confusion, sudden severe headache, vision loss, speech difficulty) bring up the FAST screen questions.
- `CHEST_PAIN_ECG_10MIN` (critical): chest pain is yes. `deadline_at` is arrival + 10 minutes, or the chest-pain answer's time + 10 minutes when the arrival time is unknown. The unanswered chest-pain questions go to the patient.
- `NG143_RED_FLAG` (critical) and `NG143_AMBER_FLAG` (high): NICE NG143 Table 2 features in children under 5, plus the Table 1 tachycardia thresholds. Source: NICE NG143, "Fever in under 5s: assessment and initial management" (published 7 Nov 2019, last updated 26 Nov 2021), each item checked on nice.org.uk on 27 Sep 2026. The fetch tool got HTTP 403; the in-app browser read the table. Red and amber are raised independently. An age range from `patient.age_years` uses the end that alerts sooner. A parent-reported seizure counts as red. SpO2 95% or less is amber in air and also on oxygen. A febrile or unwell child gets the parent screen questions.
- Contract: the amber sign "not responding normally to social cues / no smile" is the new fact `child_amber.not_responding_normally`. The red question `child_danger.no_response_to_social_cues` now asks about no response at all, as NICE separates the two.
- Engine: `RuleContext.arrived_at`, `evaluate(..., arrived_at=...)`, and a shared `raise_alert()`.

## 2026.1.0 (M1-01)

- Engine core: `Rule` protocol, versioned `Ruleset`, `evaluate(facts, now) -> RulesResult` (alerts deduped by rule id and sorted by severity; needed facts deduped by key and audience).
- NEWS2 per Royal College of Physicians, NEWS2 (2017), SpO2 Scale 1 only. Temperature is rounded half up to 1 decimal and other values to whole numbers. The score is partial when any of the 7 inputs is missing, and not applicable under 16.
- `SEPSIS_NEWS2` (critical): infection suspected and NEWS2 5 or more. The alert is marked partial (with missing inputs) on a partial NEWS2, and the missing inputs go to the nurse as needed facts.
