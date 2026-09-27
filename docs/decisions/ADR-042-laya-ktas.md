# ADR-042: Laya fine-tuned on KTAS (`laya-ktas`)

## Status

Accepted, 27 Sep 2026 ([M3-01]). This is the core build. The pytest cases (key order and omission, the fact-id mapping, the reason text, the dataset item count, T > 0), the `requires_laya` marker and the CI fixture run follow in the test phase.

## Context

The story wants a small on-device model that suggests urgency with honest confidence.

- **Laya** is `convaiinnovations/laya`: a ModernBERT-large encoder with a typed decision head, 421M parameters, Apache-2.0, not gated. It is built for calibrated decisions.
- **Its own model card** says the base checkpoint is weak zero-shot and over-confident. So it has to be fine-tuned, and a temperature fitted on our task, before anything is claimed.
- **The baseline** is the M1-04 HGB model (ADR-019), trained on the same 1,267 KTAS rows and splits.

## Decision

### Dependencies

- `laya==0.3.20`, with torch 2.14 and transformers 5.17, as the `laya` extra of `careflow-triage`.
- **Apple silicon only**, like the voice and embedding extras. torch's Linux wheels would put the CUDA stack into the lock, and CI trains nothing.
- **What imports what.** The serializer imports neither torch nor laya. Every other module imports them lazily, so the CLI and the API run without the extra.
- **The checkpoint.** The English root checkpoint is pinned at revision `55cf4c4e` and is about 850 MB. The `multilingual/` one is not used, because the state is always English.
- **Naming.** No module is named `laya`, which would shadow the package.

### The state (`laya-state/v1`, `laya_ktas/serialize.py`)

- **Content.** A small English dict with a fixed key order: age, sex, chief complaint, pain score, heart rate, SpO2, temperature, respiratory rate, systolic BP and consciousness.
- **Values.** Units sit inside the values ("112 /min"). A missing fact is left out, never written as "unknown".
- **Traceability.** `fact_ids` maps each key to its facts, so every reason cites them.
- **The question.** `KTAS_QUESTION` is one `choice` question, `urgency`, with `level_1` to `level_5` and short KTAS meanings.

### Data (`laya_ktas/dataset.py`)

- **Source.** M1-04's loader, `row_to_facts` and `splits/ktas_v1.json` (seed 42). The real data is never re-split: a split file from another CSV is an error. The leakage columns never reach a state.
- **One addition for a text model.** The row's free-text `Chief_complain` becomes `chief_complaint`. M1-04 kept only its category, but at serving time the intake gives Laya the patient's own words.
- **Items.** Built as the official notebook builds them (NandhaKishorM/laya @ `4066d5d5`): the tokenizer-config fix, `build_sequence` with the checkpoint's `max_len` 512 and `head_max_len` 192, and a one-hot target on `KTAS_expert`.
- **Augmentation.** Each epoch, a training row is shown as the kiosk sees it (no respiratory rate, BP or consciousness) with p = 0.5.

### Training (`laya_ktas/train.py`, `config/laya_ktas_v1.yaml`)

- **Model setup.** `build_model` from the checkpoint's config and `encoder/`, strict `load_state_dict` from its weights, fp32 on MPS, and gradient checkpointing on the encoder and the head.
- **Loss.** The notebook's soft cross-entropy term only. The optional RLCD term is not implemented.
- **Optimiser.** AdamW with 2.5e-5 for the encoder, 1e-4 for the head and weight decay 0.01; a cosine schedule to 1e-6; gradient clipping at 1.0.
- **Batching.** Micro-batch 4 with gradient accumulation 8 (32 rows per update).
- **Epochs.** At most 6, with an early stop on validation macro-F1 after 2 epochs without a gain.
- **Checkpoints.** The best epoch is kept in `artifacts/laya/<run_id>/` (git-ignored), as a loadable Laya checkpoint. `resume.pt` lets a stopped run continue and is deleted at the end.
- **Machine.** No cloud GPU. Docker and the local LLM are stopped while it trains.

### Calibration (`laya_ktas/calibrate.py`)

- **The fit.** One `choice` temperature, fitted as the notebook's `fit_one_temp` does (LBFGS on log T), on validation logits only. The validation rows are scored both with all vitals and kiosk-partial, because the model serves both.
- **Where it goes.** It is written into the run's `rl_agent_config.json` as `temperature = [T, 1, 1]`, without `temperature_by_options`, with `model_name = "laya-ktas"`.
- **Clamping.** Laya clamps a temperature to [0.5, 5.0] at load. The clamped value is the one used and reported.

### Reasons and the port

- **Reasons (`explain.py`).** Leave-one-fact-out. Each key is dropped in turn, one batched `predict_batch`, and the score is p(level | full) − p(level | without the key). The top 3 positive scores are kept; with no positive score, the key whose absence changes the probability most is kept, so a suggestion always has a reason. Reasons are for staff only.
- **The port (`model.py`).** `LayaUrgencyModel` wraps `laya.Agent` behind the `UrgencyModel` port. It returns an `UrgencySuggestion` with model id `laya-ktas`, the run as `model_version`, and the calibrated probability. It makes no suggestion under 15, like the baseline.
- **Serving.** It is not served yet: the HGB baseline stays the served model until M3-03 compares them.

### Report and fixtures

- **The report.** `laya evaluate` scores both models once on test, with M1-04's `metrics.py`, with all vitals and kiosk-partial. The baseline rows are recomputed from its artifact, and the `full` row is cross-checked against M1-04's report. The output is `eval/reports/triage/laya_ktas_<date>`.
- **The fixtures.** `laya fixtures` writes 50 synthetic states and their calibrated probabilities (CPU, fp32, rounded to 6 decimals) to `laya_ktas/fixtures/golden.json` for M3-02. A re-run is byte-identical.

## Consequences

### The run (`laya-ktas-ebf31b5e-s42`, real KTAS)

- **Training.** 886 training rows and 190 validation rows. All 6 epochs ran in 22 minutes on the M3 Pro's MPS (about 3.5 minutes each). The fp32 run fitted in memory with Docker stopped.
- **Validation macro-F1 by epoch:** 0.227, 0.364, 0.500, 0.483, 0.526, 0.527. The best is epoch 6.
- **Calibration.** Fitted T = 1.638, no clamp. Validation NLL fell from 1.100 to 0.981.
- **Test** (`eval/reports/triage/laya_ktas_20260927.md`; 191 rows, 37 at levels 1-2, scored once):

| Model | View | Under-triage L1-2 | Macro-F1 | ECE before -> after |
|---|---|---|---|---|
| Laya | full | 12/37 (32.4%, CI 19.6-48.5%) | 0.662 | 0.092 -> 0.097 |
| HGB (M1-04) | full | 14/37 (37.8%, CI 24.1-53.9%) | 0.625 | 0.077 -> 0.112 |
| Laya | kiosk_partial | 14/37 (37.8%, CI 24.1-53.9%) | 0.670 | 0.106 -> 0.127 |
| HGB (M1-04) | kiosk_partial | 2/37 (5.4%), over-triage 51.8% | 0.302 | 0.411 -> 0.197 |

- **Reading it.**
  - **All vitals.** Laya is slightly ahead of HGB on under-triage and macro-F1, but the intervals overlap widely. There is no clear winner on the safety metric.
  - **Kiosk vitals only.** Laya holds up, because it was trained on kiosk-partial rows. HGB, never trained without respiratory rate, blood pressure and consciousness, collapses to calling almost everyone urgent. M3-03's comparison decides what is served.
- **One acceptance criterion is not met.** Test ECE did not fall after scaling for either model. The 15-bin ECE over 191 rows is noisy; test NLL, the quantity the fit minimises, did fall for Laya (0.851 to 0.837 with all vitals, 0.847 to 0.844 kiosk-partial). The method was fixed before test was scored, and nothing was tuned on test afterwards.
- **The port.** `LayaUrgencyModel.load(run).predict(facts)` returns a valid `UrgencySuggestion` with 3 cited reasons in 0.26 to 0.62 s on MPS, reasons included.
- **The fixtures.** `golden.json` has 50 cases and is byte-identical on a re-run. The weights (1.7 GB, fp32) stay in the git-ignored `artifacts/laya/`.

### Pending for the test phase

- `pytest services/triage -k laya`, without the real CSV or the weights: key order and omission, the fact-id mapping, the reason text, the dataset item count, T > 0.
- A `requires_laya` marker for tests that need the weights.
- The CI run on the synthetic fixture.
