# ADR-053: The handbook's automated review, and the retrieval eval

Status: accepted, 27 September 2026 (SHE-304 [M2-08], the test phase of ADR-039)

## Context

ADR-039 built the clinic handbook, `rag_chunks` and hybrid retrieval. It left pending:
- the handbook's automated review;
- the labelled retrieval eval;
- the tests.

The handoff asked for more:
- check the real indexes and row-level isolation in Postgres, not fixture return values;
- report per-language hit@5 and MRR, with exact dataset and model versions;
- keep the zero-result and unavailable-embedding behaviour;
- keep the fictional handbook honestly labelled.

## Decision

### The review gate

`python -m careflow_rag.review` ticks `handbook/REVIEW.md`'s checklist for each of the 8 files.
- **Structure.** The same section ids, in the same order, in `en/` and `ar/`.
- **Denylist.** `handbook/denylist.txt` lists real hospital, health-authority and insurer names
  (UAE and international), in English and Arabic, matched as whole words. Ordinary words that are
  also brand names ("ضمان", "ثقة") are left out.
- **Back-translation.** The Arabic goes through the M2-01 tool (`scripts/i18n/backtranslate.py`),
  which now has a `handbook` source: one `handbook.<section_id>` string per section, title and
  body. They are not `safety.` strings, so a flagged section fails this review, not M2-01's gate.
  The report's `--check` sees any handbook edit, because the source's hash covers the folder.
- **The judge.** One call per file, with the English and the Arabic together. It answers:
  - fictional only (no real hospital, insurer, company or person);
  - practical only (no medical advice, diagnosis, treatment or dose);
  - does the Arabic say what the English says?

  It is the local Qwen3.5-9B, `eval_judge`'s configured fallback, a different model from the
  writer (Claude Code). The texts are passed as data, never as instructions.
- **Records.** The review writes `eval/reports/rag_handbook_review.json`, with each file's
  SHA-256, and REVIEW.md's log. `--check` needs no model, and a test runs it. It fails when a file
  changed since the review, or did not pass.

### Tests on a real database

`careflow_rag.scratch` creates a throwaway database. It migrates it with the API's own Alembic
scripts, run in a subprocess, so this package never imports the API. It adds clinics and drops the
database afterwards. The database URLs are never in a repr (see Consequences).

`pytest services/rag` uses it to check:
- the migration's real indexes (`pg_index`, `pg_am`): HNSW with `vector_cosine_ops`, the two
  partial BM25 indexes with their text configurations, and the unique key;
- forced row-level security and its policy;
- that one clinic never sees, or writes, another clinic's chunks;
- an idempotent ingest, including a changed and a removed chunk;
- each mode on a fixture handbook in both languages, with the language filter;
- the RRF scores and ordering against the formula;
- zero results: a question with no keyword match gets no BM25 result, and hybrid is the dense
  ranking alone;
- no embeddings: dense and hybrid raise `EmbeddingsUnavailableError`, which M2-09's agent turns
  into "I don't know", and BM25 still works.

### The retrieval eval

`eval/rag/run_retrieval.py` ingests the handbook into a throwaway database with the pinned
Qwen3-Embedding-0.6B. It asks each question of `eval/datasets/rag/retrieval.jsonl` in every mode,
then reports hit@5, hit@1 and MRR (to depth 20) per language and mode.
- **The questions.** 60 answerable ones, 30 in each language, covering every section in each
  language. They were written by the agent, held out, and are not real users'.
- **Out of scope.** 10 more questions (5 per language) have no answer in the handbook. They are
  unscored, and their best cosine locates the "I don't know" threshold.
- **The report.** It records the dataset hash, the handbook version, the model and its revision,
  the library and extension versions, and every answer.

## Measured (27 September 2026, reference machine)

| Language | Mode | hit@5 | hit@1 | MRR |
| --- | --- | --- | --- | --- |
| English | dense | 1.000 | 0.933 | 0.967 |
| English | bm25 | 0.800 | 0.433 | 0.586 |
| English | hybrid | 0.967 | 0.800 | 0.877 |
| Arabic | dense | 1.000 | 0.867 | 0.922 |
| Arabic | bm25 | 0.933 | 0.567 | 0.719 |
| Arabic | hybrid | 1.000 | 0.867 | 0.917 |

- **The target is met on these questions.** Hybrid hit@5 is at least 0.90 in both languages;
  that is not a claim beyond them.
- **Dense is best here.** ADR-039 foresaw this: with 25 chunks per language, every chunk is in the
  dense top 20, so a keyword match can push a dense answer down. The ablation is published as
  measured.
- **The best chunk's dense cosine:**
  - English answerable questions: 0.367 to 0.794; out of scope: 0.175 to 0.253.
  - Arabic answerable questions: 0.351 to 0.848; out of scope: 0.193 to 0.347.

  The Arabic margin is 0.004.

## Consequences

- **`config/rag.yaml` keeps `mode: dense`.** Its provisional choice holds on this eval.
- **The "I don't know" threshold is too high.** `min_score: 0.45` would refuse 9 of the 60
  answerable questions (5 English, 4 Arabic), while every out-of-scope question scored under 0.35.
  Changing it belongs to M2-09 (SHE-306), which owns the refusal and its safety tests; this ADR
  hands it the distribution.
- **A small corpus.** These numbers say little about a larger handbook, or about real users'
  wording.
- **The handbook is a research demo.** It is fictional and machine-reviewed. A real clinic needs
  its own approved sources for hours, policies, insurers and directions. None was supplied, so
  none was invented or promoted (services/rag/README.md).
- **Database URLs stay out of test reports.** A first failing run printed the test stack's local
  database URLs, with their generated passwords, through pytest's fixture repr. `ScratchDb` now
  leaves its URLs out of its repr.
