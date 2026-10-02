"""Use CareFlow's existing handbook ingest with CPU-compatible model precision."""

from __future__ import annotations

from pi_model_runtime import prepare_cpu_embeddings

if __name__ == "__main__":
    prepare_cpu_embeddings()
    from careflow_rag.ingest import main

    raise SystemExit(main())
