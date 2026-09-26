# KTAS baseline urgency models (20260927)

Data: KTAS (PLOS ONE 2019, doi:10.1371/journal.pone.0216972, CC BY 4.0), 1,267 records of adult patients (15 and over) in two Korean emergency departments, October 2016 to September 2017. Target: `KTAS_expert`.

CSV SHA-256 `ebf31b5ec517d7766851ee2082e30c3b05a578def3e2a667380c05b779f286ee`; split `ktas_v1.json` (stratified 70/15/15, seed 42): train 886, val 190, test 191. Test counts per level (1-5): 4, 33, 73, 69, 12.

Levels: 1 = most urgent. Under-triage (L1-2) = true level 1-2 predicted less urgent. Over-triage = any case predicted more urgent. ECE: top-label, 15 bins, before/after temperature scaling.

| Test set | Under-triage L1-2 | Over-triage | Macro-F1 | Accuracy | ECE before | ECE after | T |
|---|---|---|---|---|---|---|---|
| hgb `ktas-hgb-ebf31b5e-s42` | 14/37 = 37.8% (95% CI 24.1%-53.9%) | 24/191 = 12.6% (95% CI 8.6%-18.0%) | 0.625 | 0.702 | 0.077 | 0.112 | 2.18 |
| lr `ktas-lr-ebf31b5e-s42` | 9/37 = 24.3% (95% CI 13.4%-40.1%) | 22/191 = 11.5% (95% CI 7.7%-16.8%) | 0.705 | 0.707 | 0.113 | 0.153 | 1.19 |
| nurses (`KTAS_RN`), same test rows | 8/37 = 21.6% (95% CI 11.4%-37.2%) | 5/191 = 2.6% (95% CI 1.1%-6.0%) | 0.876 | 0.890 | n/a | n/a | n/a |

Temperature scaling (T fitted on val by NLL; val is therefore in-sample):

| Model | Val NLL | Val ECE | Test NLL | Test ECE |
|---|---|---|---|---|
| hgb (T=2.18) | 1.198 -> 0.952 | 0.190 -> 0.092 | 0.847 -> 0.813 | 0.077 -> 0.112 |
| lr (T=1.19) | 1.018 -> 1.009 | 0.081 -> 0.080 | 0.806 -> 0.829 | 0.113 -> 0.153 |

Nurses on all 1,267 rows: under-triage L1-2 45/246 = 18.3% (95% CI 14.0%-23.6%), over-triage 55/1267 = 4.3% (95% CI 3.4%-5.6%), macro-F1 0.807.

Level 1 has fewer than 5 test rows, so a stratified 5-fold CV over all rows is reported too (pooled out-of-fold predictions, uncalibrated):

| 5-fold CV | Under-triage L1-2 (pooled) | Over-triage (pooled) | Macro-F1 mean +- SD |
|---|---|---|---|
| hgb | 104/246 = 42.3% (95% CI 36.3%-48.5%) | 201/1267 = 15.9% (95% CI 14.0%-18.0%) | 0.586 +- 0.077 |
| lr | 80/246 = 32.5% (95% CI 27.0%-38.6%) | 224/1267 = 17.7% (95% CI 15.7%-19.9%) | 0.554 +- 0.037 |

Test confusion matrices (rows: true level 1-5; columns: predicted 1-5):

- hgb: `[[3, 1, 0, 0, 0], [0, 20, 9, 3, 1], [1, 8, 51, 12, 1], [0, 2, 5, 56, 6], [1, 0, 2, 5, 4]]`
- lr: `[[4, 0, 0, 0, 0], [0, 24, 4, 4, 1], [1, 10, 51, 9, 2], [0, 3, 5, 47, 14], [0, 0, 2, 1, 9]]`
- nurses: `[[3, 1, 0, 0, 0], [0, 26, 5, 2, 0], [0, 2, 65, 5, 1], [0, 1, 2, 64, 2], [0, 0, 0, 0, 12]]`

Caveats: adults in two Korean EDs; these results say nothing about children, UAE or UK patients. Level 1 has very few cases, so its intervals are wide; no target is claimed as achieved. The model only suggests a level: the safety rules run first and a nurse confirms every level.

Research prototype. Synthetic data only in the product; this dataset is public research data.
