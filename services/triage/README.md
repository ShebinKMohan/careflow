# careflow-triage

The CareFlow urgency model baselines (M1-04). The package covers:

- loading the KTAS data and profiling it;
- one shared feature path from `StructuredFacts`;
- seeded splits;
- logistic regression (`lr`) and HistGradientBoosting (`hgb`) training, with temperature scaling;
- metrics, reports and the model card;
- the `UrgencyModel` inference port that the intake graph uses (M1-05 `suggest_urgency`).

It also covers Laya fine-tuned on KTAS (M3-01, ADR-042): `laya_ktas/`, model id `laya-ktas`, behind the same port. ONNX export comes in M3-02.

```bash
uv run careflow-triage ktas download        # data/raw/ktas.csv (see DATA.md); never committed
uv run careflow-triage ktas profile         # eval/reports/triage/ktas_profile.md
uv run careflow-triage train --model hgb    # artifacts/<model_version>/, report, MODEL_CARD.md
uv run careflow-triage train --model lr
uv run pytest services/triage               # passes without the real CSV (requires_ktas tests skip)
```

```python
from pathlib import Path

from careflow_triage.inference import UrgencyModel

model = UrgencyModel.load(Path("services/triage/artifacts/ktas-lr-ebf31b5e-s42"))
# UrgencySuggestion: level, scale, display_label, confidence, 3 reasons, model_version
suggestion = model.predict(facts)
```

- `config/ktas_mapping.yaml` maps each KTAS column to a fact path. Each code is verified against the paper, and the complaint keywords are an assumption.
- `config/triage_scales.yaml` holds the display labels for KTAS, ESI, CTAS and MTS. This is a display mapping only: the scales are not clinically equivalent.
- Features come only from `facts_to_features(facts)`, which training and serving share. The leakage columns (the nurses' and experts' levels, diagnosis, disposition, error group, length of stay, KTAS duration and mistriage) are never features.
- A patient known to be under 15 gets no suggestion (`OutOfScopeError`), because the KTAS data covers adults only.
- Results, limits and the nurse comparator are in `MODEL_CARD.md` and `eval/reports/triage/`. The decisions are in ADR-019.

## Laya (M3-01)

Laya (`convaiinnovations/laya`, a ModernBERT-large encoder with a decision head, 421M, Apache-2.0) is fine-tuned on the same KTAS splits, on MPS only. It needs the Apple-silicon `laya` extra (torch):

```bash
uv sync --all-packages --extra laya         # plus the other extras `make up` installs
uv run careflow-triage laya train           # config/laya_ktas_v1.yaml -> artifacts/laya/<run_id>/
uv run careflow-triage laya calibrate       # T fitted on validation, written into the run
uv run careflow-triage laya evaluate        # eval/reports/triage/laya_ktas_<date>.{json,md}, beside the baseline
uv run careflow-triage laya fixtures        # laya_ktas/fixtures/golden.json (50 synthetic states)
```

- `laya_ktas/serialize.py` (`facts_to_state`, the `laya-state/v1` input contract) is pure Python; the API and M3-02 import it without torch.
- `LayaUrgencyModel.load(run_dir).predict(facts)` returns an `UrgencySuggestion` (model id `laya-ktas`) with the top 3 leave-one-fact-out reasons.
- Stop Docker and the local LLM while training: a full fine-tune needs about 10 GB.

The model only suggests a level. The safety rules run first, and a nurse confirms or overrides every level.

## API model selection

Set `TRIAGE_MODEL_KIND=laya` to use an installed fine-tuned, calibrated Laya checkpoint. Set
`TRIAGE_MODEL_PATH` to pin its directory, or leave the path unset to select the newest installed
checkpoint under `artifacts/laya/`. `TRIAGE_MODEL_DEVICE=cpu` and `TRIAGE_MODEL_THREADS=2` are the
API defaults. `scripts/stack.py env` preserves these settings from the environment or `.env`.
Restart the API after changing them; changing an environment file does not reload a running model.

The loader requires `fine_tuned: true`, the `laya-ktas` model name, calibration metadata, and a
positive finite temperature. If the selected artifact cannot load, the API reports no suggestion
with the selected model kind and error category. It does not substitute HGB. To select HGB
explicitly, set `TRIAGE_MODEL_KIND=hgb` and clear the Laya `TRIAGE_MODEL_PATH` or point it to the
desired HGB artifact. Installations without explicit selection retain the HGB default.

Adaptive adult intake uses the selected urgency model. Registration-only reception visits and
children retain the nurse-only urgency path. Staff receive the actual `model_id`, `model_version`,
and cited fact IDs. Laya explanations describe how a fact affected the probability of the selected
KTAS class; support for a lower-acuity class is not described as raising urgency.

Research prototype. Synthetic data only in the product; KTAS is public research data used only to train and evaluate the baseline. Not for clinical use.
