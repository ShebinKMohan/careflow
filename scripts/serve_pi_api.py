"""Serve the unchanged CareFlow API with its original models on a Pi CPU."""

from __future__ import annotations

import uvicorn

from pi_model_runtime import CpuQwenEmbedder

if __name__ == "__main__":
    from careflow_api.main import create_app

    app = create_app()
    # AskService already accepts an Embedder port. Only its CPU execution and
    # memory lifetime change; the original retrieval/agent behavior is retained.
    app.state.ask._embedder = CpuQwenEmbedder()
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000,
        timeout_graceful_shutdown=3,
    )
