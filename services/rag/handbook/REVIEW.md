# Handbook review (M2-08)

Every handbook file passes an automated review before it is ingested. The owner may spot-check;
that never blocks.

## Checklist (per file)

1. The same section ids, in the same order, in `en/` and `ar/`.
2. Fictional only: "CareFlow Demo Clinic", "Insurer A/B/C"; no real hospital, insurer or person
   (the denylist test).
3. Practical information only: no medical advice, no diagnosis, no dosing.
4. The Arabic passes the M2-01 back-translation check (`same_meaning` true, chrF at least 40).
5. An `eval_judge` pass, by a different model from the writer, confirms items 2 to 4.

## How it runs

```sh
scripts/with_llm.sh uv run python scripts/i18n/backtranslate.py   # item 4, with the M2-01 strings
scripts/with_llm.sh uv run python -m careflow_rag.review         # items 1 to 5; writes the log
uv run python -m careflow_rag.review --check                     # no model: the log still holds
```

Written by `agent:claude-code/claude-opus-5-5` from `OUTLINE.md`. The judge is the local model
(`eval_judge`'s configured fallback), a different model from the writer. The Arabic is machine
checked only: no native speaker reviewed it, and no accuracy figure is claimed. The handbook is
fictional; a real clinic's handbook needs the clinic's own approved sources (README).

## Log

<!-- review-log:start (written by python -m careflow_rag.review) -->

Reviewed 2026-09-27T18:36:29+00:00; reviewed_by: "agent:local/qwen3.5-9b-4bit" (served: `mlx-community/Qwen3.5-9B-MLX-4bit@938d8919941c6e7efd3c7150eff7fe9d12afa631`, `mlx-community/SmolLM2-135M-Instruct-8bit`); written_by: "agent:claude-code/claude-opus-5-5". 8 of 8 files passed. Details: `eval/reports/rag_handbook_review.json`.

| File | 1. Same sections | 2. Denylist | 2. Judge: fictional | 3. Judge: practical | 4. Back-translation (chrF min) | 5. Judge: Arabic matches | Passed |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `departments.md` | yes | clean | yes | yes | pass (60.6) | yes | **passed** |
| `directions.md` | yes | clean | yes | yes | pass (60.0) | yes | **passed** |
| `facilities.md` | yes | clean | yes | yes | pass (62.1) | yes | **passed** |
| `hours.md` | yes | clean | yes | yes | pass (66.0) | yes | **passed** |
| `insurance.md` | yes | clean | yes | yes | pass (63.8) | yes | **passed** |
| `parking.md` | yes | clean | yes | yes | pass (61.3) | yes | **passed** |
| `pharmacy.md` | yes | clean | yes | yes | pass (80.6) | yes | **passed** |
| `visitors.md` | yes | clean | yes | yes | pass (42.8) | yes | **passed** |

<!-- review-log:end -->
