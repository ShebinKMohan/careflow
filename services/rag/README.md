# careflow-rag

CareFlow clinic knowledge ingest and hybrid retrieval (M2-08, ADR-039), and the phone-only
"ask the clinic" agent (M2-09, ADR-040).

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
- `careflow_rag.agent.AskAgent`: a fixed-shape LangGraph (classify, then refuse and flag, or plan,
  run the tools, answer and check the citations). The medical classifier is the lexicon in
  `medical_lexicon.yaml` plus the `conversation` model, and it fails safe: a medical question gets
  the fixed refusal and a nurse flag, never an answer. The API injects the tools (`AskPorts`);
  this package never imports `careflow_api`. Settings: `config/rag.yaml`. Phone only (ADR-006).
- `careflow_rag.review`: the handbook's automated review gate (`REVIEW.md`): the section
  structure, the real-name denylist (`handbook/denylist.txt`), the M2-01 back-translation of the
  Arabic, and a judge from a different model than the writer. `--check` verifies the recorded
  review without a model; `pytest services/rag` runs it.
- `careflow_rag.scratch`: a throwaway database migrated like the API's, for the tests and the
  retrieval eval (`eval/rag/run_retrieval.py`, report `eval/reports/rag_retrieval.md`).

No patient data ever enters the handbook index.

**The handbook is a labelled research demo.** Its clinic, insurers, hours, rules and rooms are
invented, and its review is automated: it checks the Arabic against the English, not against a
real clinic. A real deployment needs the clinic's own approved documents for its actual hours,
policies, insurers and directions, each with its source. None has been supplied, so none is
included, and the fictional text must never be presented as a real clinic's.
