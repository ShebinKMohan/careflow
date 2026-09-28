# Staff clinical review evidence — 29 September 2026

Research prototype. Synthetic data only. Not for clinical use.

The existing navy/white/cobalt staff workspace now displays actual API model identity, urgency scale/level, deterministic safety-rule identifiers and their ruleset version. Model reason fact references retain provenance and inspectable sources. Clinical review groups onset, symptoms, pain, observations, sourced history, medicines and allergies. Empty data is not represented as normal or negative history. Adaptive caregiver registration is separate from the actual patient record.

## Local validation

- Staff type checking and full lint passed after the clinical integration.
- The targeted clinical/reception/live-refresh/doctor-source suite passed 42 tests. This includes the caregiver separation and failed-facts-refresh regressions.
- A final partial-alert-evidence case was added; the clinical and English/Arabic layout suites then passed 11 tests (9 clinical and 2 layout).
- The existing nurse library, live session, decision failure, reception and layout set passed 31 tests before the final two caregiver/refresh cases were added.
- A Chrome browser check against the existing local development app rendered the fixture-backed nurse session. Axe reported zero serious/critical violations. At 390px viewport width, document width did not exceed the viewport.
- Browser-visible model identity was `careflow-ktas-gbm`, version `0.1.0`, because that is the supplied fixture's actual identity. This is UI evidence, not proof of the currently loaded model runtime.
- The browser-visible safety rule was `CHEST_PAIN_ECG_10MIN`, ruleset `2026.1.0`. These are source API identifiers, not a fabricated hospital emergency code.

## Screenshots

- [Desktop clinical review](clinical-review-desktop.png)
- [390px clinical review](clinical-review-mobile.png)

## Proof boundary

The tests use synthetic API fixtures. Actual model loading, national/demo identity verification, session creation and complete patient-to-nurse clinical journeys are owned by the live integration validation. Camera preview does not produce clinical facts; simulated device readings are explicitly labelled. No live-model readiness or clinical validation is inferred from these screenshots or test counts.
