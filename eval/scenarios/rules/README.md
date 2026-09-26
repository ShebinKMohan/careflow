# Rules scenarios

One synthetic scenario per file for the safety rules engine (`services/rules`). `services/rules/tests/test_scenarios.py` validates every file against `_schema.json` and asserts that the alerts raised equal `expect_alerts` exactly. The eval harness (M1-18) reuses these files.

- `facts`: contract fact paths (`FactKey`), plus `symptoms` (one symptom per entry) and `chief_complaint`, each with a `value`, a `provenance` and a `recorded_at`.
- `expect_needed` (optional) lists facts the rules must ask for; `expect_deadline_at` (optional) is the chest-pain ECG deadline.
- `covers` names the feature a scenario exists for, e.g. `red:child_danger.grunting` or `amber:vitals.heart_rate`. The tests check that every NICE NG143 red and amber feature is covered.
- Files are read with `yaml.safe_load`, so quote `"yes"`/`"no"` answers, timestamps and descriptions.

No alert never means "nothing is wrong": the benign scenarios only show that these rules stay quiet.

Research prototype. Synthetic data only. Not for clinical use.
