# Model card: CareFlow KTAS baseline urgency models



- **What:** logistic regression (`lr`) and HistGradientBoosting (`hgb`) classifiers that suggest a KTAS urgency level (1 = most urgent) from 14 intake features, calibrated with temperature scaling. `UrgencyModel.predict(facts)` returns the level, a display label (KTAS, ESI, CTAS or MTS; a display mapping only, the scales are not clinically equivalent), a calibrated confidence and the top three SHAP reasons.
- **Intended use:** a suggestion for the triage nurse in a research prototype. The deterministic safety rules run first and a nurse confirms or overrides every level. Not for clinical use.
- **Data:** KTAS, Moon et al., PLOS ONE 2019 (doi:10.1371/journal.pone.0216972, CC BY 4.0): 1,267 randomly selected records of adult patients in two Korean emergency departments, October 2016 to September 2017 (the article, checked 27 Sep 2026), labelled by triage experts (`KTAS_expert`). The rows are never committed or redistributed.
- **Features:** age, sex, arrival mode, injury, ACVPU, pain (yes/no), pain score, SBP, DBP, heart rate, respiratory rate, temperature, SpO2 and a keyword-mapped complaint category, all read from `StructuredFacts` by one function (`facts_to_features`), the same at training and serving. Excluded (leakage): the nurses' and experts' levels, diagnosis, disposition, error group, length of stay, KTAS duration and mistriage.
- **Evaluation:** seed 42, stratified 70/15/15 split; test set results below (full report: `eval/reports/triage/ktas_baseline_20260927.md`).

| Model | Version | Under-triage L1-2 (95% CI) | Macro-F1 | ECE before -> after |
|---|---|---|---|---|
| hgb | `ktas-hgb-ebf31b5e-s42` | 14/37 = 37.8% (95% CI 24.1%-53.9%) | 0.625 | 0.077 -> 0.112 |
| lr | `ktas-lr-ebf31b5e-s42` | 9/37 = 24.3% (95% CI 13.4%-40.1%) | 0.705 | 0.113 -> 0.153 |

- **Limitations:** adults only (the youngest is 15): a patient known to be under 15 gets no suggestion (`OutOfScopeError`), and a child whose age is unknown is outside the data. Two Korean EDs: the results say nothing about UAE or UK patients, other triage systems or other hospitals. Level 1 has 26 cases in total, so its estimates are very uncertain; no target is claimed as achieved. SpO2 is missing for one of the two hospitals. The complaint categories are CareFlow's keyword mapping of short English text.
- **Ethics and safety:** the model never decides. Rules first (safety rule 1), nurse confirms (safety rule 3). Laya (M3) must beat this baseline, and every baseline is published.

Research prototype. Synthetic data only in the product. Not for clinical use.
