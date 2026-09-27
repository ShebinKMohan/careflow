# ADR-039: The clinic knowledge base and hybrid retrieval

## Status

Accepted, 27 Sep 2026 ([M2-08]). This is the core build. The automated handbook review, the labelled retrieval eval and its report, and the unit tests follow in the test phase.

The test phase is done (SHE-304, docs/evidence/M2-08.md): ADR-053 records the review gate, the eval and what it measured.

## Context

"Ask the clinic" (M2-09) must answer from the clinic's own documents, with citations, and never from model memory. Retrieval quality caps answer quality, so retrieval is built and measured first.

- The infrastructure already had pgvector 0.8 and pg_textsearch 1.4 (M0-05), and both `english` and `arabic` text search configurations exist.
- The handbook had to be written from scratch.

## Decision

### The handbook

- **The clinic.** `services/rag/handbook/{en,ar}` is the fictional "CareFlow Demo Clinic", with insurers "Insurer A/B/C" and no real names.
- **Content.** Eight topics (`OUTLINE.md`) in 25 sections, each starting with `<!-- section: <id> -->`. The same ids are used in both languages.
- **Scope.** The sections cover practical information only: hours, departments, insurance, visitors, pharmacy, parking, directions and facilities. Never medical advice.
- **Consistency.** The departments and rooms match `config/rooms.yaml`.
- **Arabic.** Written from the English, with Western digits (ADR-032). It is not validated by native speakers.
- **Review.** `REVIEW.md` holds the checklist. Its automated gate (back-translation, the real-name denylist, a judge from a different model) is part of the test phase.

### Storage (migration 0009)

- **The table.** `rag_chunks` stores one row per chunk and language: the section id, chunk number, title, body, a 1024-dimension embedding, the content hash, and the handbook version. It is unique on `(clinic_id, section_id, chunk_no, lang)` and clinic-scoped by forced RLS.
- **Indexes.**
  - HNSW on the embedding (`vector_cosine_ops`).
  - Two partial BM25 indexes, `rag_bm25_en` (`english`) and `rag_bm25_ar` (`arabic`), on `lang`. They are named, because `to_bm25query` addresses an index by name.
- **Why BM25.** pg_textsearch works here, so no FTS fallback was needed.

### Embeddings

- **The model.** `Qwen/Qwen3-Embedding-0.6B` (Apache-2.0, ungated) at revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`, through sentence-transformers 5.7.
- **Running it.** It uses MPS when available and normalises vectors. Questions use the model's `query` prompt, as its card shows.
- **Loading.** It is loaded once per process (about 1.2 GB; checked for at least 15 GB free before the download).
- **Extra.** The `embeddings` extra is Apple-silicon only, like the voice extras, because torch's Linux wheels would bring the CUDA stack into the lock. CI and tests use `HashEmbedder`, a deterministic, model-free stand-in with 1024 dimensions.
- **Tracking the embedder.** `handbook_version` records which embedder wrote the vectors, and a change of embedder re-embeds everything.

### Ingest

`python -m careflow_rag.ingest <dir> --clinic <id>`, or `make rag-ingest` for both dev clinics.

- **Chunking.** It parses the sections and splits any over 350 words at paragraph breaks.
- **Idempotent.** It embeds only chunks whose hash changed, upserts on the unique key, and deletes chunks that no longer exist. A second run writes nothing: 50 chunks, 0 written.
- **Access.** It connects as the app, with the clinic's RLS scope.

### Retrieval

`retrieve(db, query, lang, clinic_id, embedder=, k=5, mode=)` returns `RetrievedChunk`s, now in the contracts.

- **`dense`.** The top 20 by cosine distance.
- **`bm25`.** The top 20 through the language's index. A chunk with no query term scores 0 and is not a match. The first version ranked those non-matches, and they swamped the fusion.
- **`hybrid`.** Reciprocal rank fusion, `sum(1 / (60 + rank))`, on ranks only.
- **Language.** Every mode filters by the question's language. The SQL is written out once per language, so the planner can use the partial index.
- **Citations.** `citation()` maps a chunk to the API citation: `doc_id` is the section and `chunk_id` is `<section>:<chunk>`. (Amended by ADR-040: the first version used `#`, which the API's Identifier pattern rejects.)

## Consequences

- M2-09 can retrieve clinic answers with their citations in either language, without patient data ever entering the index.
- **Smoke check** (not the eval): on ten sample questions (five English, five Arabic), dense retrieval put the right section first for nine. After the zero-score fix, hybrid had it in the top 5 for nine.
- **A known weakness of the fusion here.** With 25 chunks per language, every chunk is in the dense top 20. So any keyword match gets both contributions and can push a dense-only answer out of the top 5.
- **Choosing a mode.** The eval will publish hit@5 and MRR for all three modes honestly, even if dense-only wins. M2-09 picks its mode and its "I don't know" cosine threshold from that report.
- **Pending for the test phase:**
  - `eval/datasets/rag/retrieval.jsonl` (about 60 questions) and `eval/rag/run_retrieval.py`, writing `eval/reports/rag_retrieval.json` and `.md` (hybrid hit@5 ≥ 0.90 is a target, not a claim);
  - the automated handbook review and the denylist test;
  - `pytest services/rag`: chunking, RRF arithmetic, retrieval on a fixture handbook, and ingest idempotence.
