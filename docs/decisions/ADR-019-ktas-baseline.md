# ADR-019: KTAS data, features and baseline urgency models

## Status

Accepted, 27 Sep 2026 ([M1-04]).

## Context

The nurse needs an urgency suggestion to confirm, and Laya (M3) needs a baseline to beat. The public KTAS data is Moon et al., PLOS ONE 2019 (CC BY 4.0). It covers 1,267 adults (15 and over) in two Korean EDs from October 2016 to September 2017, with the experts' level (`KTAS_expert`) and the triage nurses' level (`KTAS_RN`).

The issue expects:

- data from Kaggle, or the paper's S1 Appendix without a login;
- one feature function shared by training and serving;
- LR and HGB models with temperature scaling;
- a `UrgencyModel.predict(facts)` port for M1-05.

Several details are not settled by the issue or by the M0-02 contracts:

- how the columns are coded;
- three answers (pain present, injury, arrival mode) have no fact paths;
- `UrgencySuggestion` has no field for a display label;
- level 1 has only 26 cases.

## Decision

### Data

- **Source.** `careflow-triage ktas download` tries these sources in order:
  1. an existing CSV;
  2. the PLOS S1 xlsx, checked against its SHA-256 and converted with openpyxl;
  3. `kaggle datasets download`, only when `~/.kaggle/kaggle.json` exists (the slug is an assumption, untested);
  4. a seeded 60-row synthetic fixture, whose artifacts are `fixture-*` and whose reports say NOT A RESULT.
- **What is committed.** `services/triage/data/` and `artifacts/` are git-ignored. Only these are committed: aggregate reports, the split indices (tied to the CSV's SHA-256), the model card, and `services/triage/DATA.md`.
- **Where the data doc lives.** The issue asks for `data/README.md`, but the acceptance criterion needs `git ls-files services/triage/data` to be empty. So the committed file is `DATA.md`, and each download copies it to `data/README.md`. That keeps the CC BY attribution next to the rows.
- **Codes are verified, not assumed.** Every code in `config/ktas_mapping.yaml` was matched against the paper's Table 1 counts. For example:
  - Sex: 1 = female (606), 2 = male (661).
  - Arrival: 1 walking, 2 public ambulance, 3 private vehicle, 4 private ambulance, 5-7 other.
  - Injury: 2 = yes (244).
  - Mental: 1-4 = A/V/P/U.
  - Pain: 1 = yes (714).

  Each entry records its evidence.
- **Placeholders.** The loader reads these as missing: `측불` (unmeasured vitals), `#NULL!` (`NRS_pain`) and the empty SpO2 of one hospital. It also accepts the quirks reported for Kaggle's CSV (`;`, decimal commas, cp949/latin-1), which are tested on synthetic files. Ages with decimals are truncated to whole years.

### Features and contracts

- **One feature path.** `row_to_facts(row)` builds `StructuredFacts` with provenance:
  - EHR for the patient;
  - nurse-confirmed for vitals and answers, as the triage nurse recorded them;
  - model-inferred (`ktas_keyword_map`) for the complaint category.

  `facts_to_features(facts)` is the only feature function. It yields 14 features in a pinned order. Missing values are NaN, and categoricals are category indices. Serving calls the same function.
- **Leakage guard.** `KTAS_RN`, `KTAS_expert`, diagnosis, disposition, error group, length of stay, KTAS duration and mistriage are never read into facts. A test pins the feature list, and another test changes those columns without the features changing.
- **Complaint categories are an assumption.** An ordered keyword map turns the free-text complaint into one of CareFlow's complaint codes. A keyword matches at the start of a word; a trailing space means the whole word. This is CareFlow's mapping, not the paper's.
- **Range guard.** Values outside plausible ranges are dropped instead of failing the contract: SpO2 0-100, pain 0-10 and age 0-150.
- **Contract additions.**
  - `pain_present`, `injury` and `arrival_mode` are new `StructuredFacts` fields. Each has a `FactKey` value, a Questionnaire item and a FHIR mapping row. Arrival mode gets a new CareFlow code system.
  - `UrgencySuggestion.display_label` is new: a string for display only.
  - oasdiff counts the new `FactKey` values as breaking, because they can appear in responses (`needed_facts[].key`, `missing_inputs`). The 12 reviewed lines are acknowledged in `packages/contracts/oasdiff-err-ignore.txt` under "M1-04 (ADR-019)". Every other breaking change still fails. The M1-02 lines were removed, because main already contains them.

### Models

- **Splits.** The split is stratified 70/15/15 with seed 42. Level 1 has 4 test rows, so a stratified 5-fold CV over all rows is also reported, using pooled out-of-fold predictions.
- **LR.** As the issue specifies:
  - numeric features: median imputation with missing indicators, then scaling;
  - categorical features: one-hot encoding that ignores unknown values;
  - `LogisticRegression(class_weight="balanced", max_iter=2000)`, without `multi_class`.
- **HGB.** `HistGradientBoostingClassifier(class_weight="balanced", random_state=42)`, with native NaN handling.
  - The categorical indices go in as numbers; the trees split them anyway.
  - The grid is learning rate {0.05, 0.1} × max leaf nodes {15, 31}. It is chosen on validation by macro-F1, then NLL, and the chosen setting is 0.05 / 15.
- **Calibration.** A scalar T is fitted on validation NLL with `minimize_scalar` (bounds 0.05-10) and stored in the artifact.
- **Calibration finding: temperature scaling does not transfer to the test split.**
  - On validation it lowers NLL and ECE: HGB ECE 0.190 → 0.092 (T = 2.18); LR ECE 0.081 → 0.080 (T = 1.19).
  - On the test split, ECE rises: LR 0.113 → 0.153; HGB 0.077 → 0.112. HGB's test NLL still falls (0.847 → 0.813), but LR's rises (0.806 → 0.829).
  - Validation accuracy is 0.62-0.64 but test accuracy is 0.70-0.71, so a T that corrects overconfidence on validation makes the models underconfident on test.
  - Both before and after values are reported, and nothing is tuned on the test split.
- **Metrics.** The report covers:
  - under-triage of true levels 1-2, with Wilson 95% CIs;
  - over-triage;
  - macro-F1 and accuracy;
  - top-label ECE (15 bins);
  - confusion matrices;
  - the nurse comparator (`KTAS_RN`) on the same test rows and on all rows.

  No target is claimed as achieved.
- **Default model.** The default for the M1-05 port is `lr`. It under-triages less:
  - test: 9/37 (24.3%) against 14/37 (37.8%);
  - CV, pooled: 32.5% against 42.3%.

  Its test macro-F1 is also higher (0.705 against 0.625), although HGB's CV macro-F1 is higher (0.586 against 0.554). This picks a baseline. It does not claim the model is fit for use; the nurses themselves under-triage 21.6% on the same rows.

### Inference

- `UrgencyModel.predict(facts)` returns a contract-valid `UrgencySuggestion`:
  - the level and the calibrated confidence;
  - the display label for KTAS, ESI, CTAS or MTS, from `config/triage_scales.yaml`, which says "display mapping; scales are not clinically equivalent";
  - `model_version`, for example `ktas-lr-ebf31b5e-s42` (kind, CSV hash and seed);
  - the top 3 |SHAP| features for the predicted class.
- **Explainers.** LR uses `shap.LinearExplainer` on the transformed background, with the one-hot columns summed back to their feature. HGB uses `shap.TreeExplainer`, which in shap 0.52 supports multiclass HistGradientBoosting with raw output. A test checks that the SHAP values plus the base value equal each class's logit.
- **Reason wording.** A reason reads, for example, "Heart rate 128/min made KTAS 2 more likely", and cites the fact ids behind it. It does not say "raised urgency" as the issue's example does. A SHAP value towards the predicted class says which level the feature favours, not whether the patient is more urgent.
- **Latency.** Single-row prediction runs with OpenMP limited to one thread. The limit uses a `ThreadpoolController` built once per model; building one per call with `threadpool_limits` cost about 1 ms. On the 191 test rows (M3 Pro):
  - HGB: median 3.9 ms (max 4.1 ms), against 20.5 ms (max 28.8 ms) with OpenMP threads for one row.
  - LR: median 1.2 ms (max 1.6 ms).
- **The 20 ms budget is not a CI gate.** The owner decisions say speed targets are measured and published, never CI gates. The first CI run failed on a single HGB call of 21.4 ms on a GitHub runner.
  - The test now asserts the budget on the reference machine, meaning any run without `CI=true`.
  - On a CI runner, it warns with the measured numbers when a call goes over the budget.
  - The reasons and `model_version` assertions run everywhere.
  - This is recorded in `docs/FLAKY.md`.
- **Adults only.** A patient whose recorded age is under 15 (in years or months) gets `OutOfScopeError` instead of a suggestion. A child with no recorded age cannot be detected, which the model card states.

## Consequences

- M1-05's `suggest_urgency` loads the `lr` artifact and calls `predict(facts)` after the rules have run. It handles `OutOfScopeError` by showing no suggestion. M1-12 serves the suggestion, and the nurse confirms every level.
- M3-01 (Laya) must beat these baselines on the same split, reported with the same metrics and the nurse comparator.
- Calibration should be revisited when Laya is evaluated. One option is fitting T on pooled out-of-fold logits instead of the 190 validation rows.
- The results are Korean adult ED data only. They say nothing about UAE or UK patients, children, or other triage systems.
