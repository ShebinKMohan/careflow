# careflow-rag

CareFlow clinic knowledge ingest and hybrid retrieval (M2-08, ADR-039).

Research prototype. Synthetic data only. Not for clinical use.

- `handbook/`: the fictional CareFlow Demo Clinic handbook, in `en/` and `ar/`, with the same
  section ids (`<!-- section: visitors.hours -->`). `OUTLINE.md` lists the sections; `REVIEW.md`
  holds the review checklist and log.
- `careflow_rag.ingest`: `make rag-ingest` (or `uv run --package careflow-rag --extra embeddings
  python -m careflow_rag.ingest services/rag/handbook --clinic <clinic_id>`) embeds the changed
  chunks with Qwen3-Embedding-0.6B and upserts them into `rag_chunks`. Running it again changes
  nothing.
- `careflow_rag.retrieve.retrieve()`: `dense`, `bm25` or `hybrid` (reciprocal rank fusion), always
  filtered by the question's language.

No patient data ever enters the handbook index.
