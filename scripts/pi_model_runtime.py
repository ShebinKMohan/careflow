"""Load the original pinned embedding model in float32 on ARM Linux.

The Pi lacks efficient bfloat16 matrix operations. This changes the inference
precision and device only; retrieval, model weights, prompts and dimensions
remain those of CareFlow's existing QwenEmbedder.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from collections.abc import Sequence
from pathlib import Path

from careflow_rag.embed import QwenEmbedder

_WORKER_LOCK = threading.Lock()


class CpuQwenEmbedder(QwenEmbedder):
    """Use the original embedder in a short-lived process to release its CPU RAM.

    The API and the 9B language model share an 8 GB Pi. Keeping the embedding
    weights resident in the API would push the language model into swap.
    """

    def _encode(self, kind: str, texts: Sequence[str]) -> list[list[float]]:
        with _WORKER_LOCK:
            # Fixed interpreter/script argv; question text travels through stdin.
            result = subprocess.run(  # noqa: S603
                [sys.executable, str(Path(__file__).resolve())],
                input=json.dumps({"kind": kind, "texts": list(texts)}),
                text=True,
                capture_output=True,
                check=True,
                timeout=180,
            )
        return json.loads(result.stdout)

    def documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._encode("documents", texts)

    def query(self, text: str) -> list[float]:
        return self._encode("query", [text])[0]


def prepare_cpu_embeddings() -> None:
    import torch
    from sentence_transformers import SentenceTransformer

    from careflow_rag import embed

    torch.set_num_threads(2)
    key = (embed.MODEL_ID, embed.MODEL_REVISION)
    with embed._MODELS_LOCK:
        if key not in embed._MODELS:
            embed._MODELS[key] = SentenceTransformer(
                embed.MODEL_ID,
                revision=embed.MODEL_REVISION,
                device="cpu",
                model_kwargs={"torch_dtype": torch.float32},
            )


if __name__ == "__main__":
    body = json.load(sys.stdin)
    prepare_cpu_embeddings()
    original = QwenEmbedder()
    vectors = (
        [original.query(body["texts"][0])]
        if body["kind"] == "query"
        else original.documents(body["texts"])
    )
    json.dump(vectors, sys.stdout)
