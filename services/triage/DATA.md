# KTAS data for the urgency baseline

The M1-04 baselines train on public research data:

> Moon S-H, Shim JL, Park K-S, Park C-S (2019). Triage accuracy and causes of mistriage using the Korean Triage and Acuity Scale. *PLOS ONE* 14(9): e0216972. doi:10.1371/journal.pone.0216972. S1 Appendix. Licence: CC BY 4.0.

The data has 1,267 randomly selected records of adult patients in two Korean emergency departments, October 2016 to September 2017, with 24 columns. Two of them are the level given by the triage nurse (`KTAS_RN`) and the level given by triage experts (`KTAS_expert`, the target). Kaggle hosts the same data as "Emergency Service - Triage Application".

## Rules

- **Rows are never committed or redistributed.** `services/triage/data/` and `services/triage/artifacts/` are git-ignored, and `git ls-files services/triage/data` is empty. The only KTAS outputs committed are:
  - aggregate metrics (`eval/reports/triage/`);
  - the split row indices (`splits/ktas_v1.json`);
  - `MODEL_CARD.md`.
- The product itself uses synthetic data only. This dataset only trains and evaluates the baseline.
- Every code in the data was checked against the paper's Table 1 and recorded in `config/ktas_mapping.yaml`.

## Get the data

```bash
uv run careflow-triage ktas download
```

This writes `services/triage/data/raw/ktas.csv`, then copies this file to `services/triage/data/README.md`. It tries these sources in order:

1. **An existing CSV** at that path, used as it is.
2. **The PLOS ONE S1 Appendix**, from `https://journals.plos.org/plosone/article/file?type=supplementary&id=10.1371/journal.pone.0216972.s001`. No login is needed.
   - The download is checked against SHA-256 `f631c951178799179d0fbd74fe979464b955ea1b426222bf015b163b8a24beb0`, then converted to CSV with openpyxl.
   - The xlsx is kept as `data/raw/ktas_plos_s1.xlsx`.
   - The CSV used for the committed results has SHA-256 `ebf31b5ec517d7766851ee2082e30c3b05a578def3e2a667380c05b779f286ee`.
3. **`kaggle datasets download`**, only when `~/.kaggle/kaggle.json` exists. The slug `ilkeryildiz/emergency-service-triage-application` is an assumption: this path is untested, because the project uses no Kaggle account.
4. **The synthetic 60-row fixture**, written to `data/raw/ktas_fixture.csv`. Its models are labelled `fixture-*`, its reports say NOT A RESULT, and the `requires_ktas` tests are skipped.

Then:

```bash
uv run careflow-triage ktas profile        # counts per level -> eval/reports/triage/ktas_profile.md
uv run careflow-triage train --model hgb
uv run careflow-triage train --model lr    # artifacts, report and MODEL_CARD.md
```

The split file records the CSV's SHA-256. With a different CSV (Kaggle's, for example), `test_real_ktas.py` fails on the checksum. Training then makes new seed-42 splits and rewrites `splits/ktas_v1.json`, and the rewrite shows up as a git change.

## File format

`load_ktas()` accepts:

- a `,` or `;` separator;
- UTF-8, cp949 or latin-1 encoding;
- decimal commas (`36,6`).

These placeholders are read as missing:

- `#NULL!` (`NRS_pain`);
- `측불` (vitals that were not measured);
- `??`, `NA` and empty cells.

The PLOS file is comma-separated UTF-8. Its only quirks are placeholders and ages with decimals, which are truncated to whole years. The Kaggle quirks (`;`, decimal commas, latin-1) were reported for Kaggle's CSV but not seen in the PLOS file. They are handled and tested on synthetic files.
