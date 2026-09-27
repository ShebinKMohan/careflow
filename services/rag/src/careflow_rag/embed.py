"""Embeddings for the handbook and the questions (M2-08).

`QwenEmbedder`: `Qwen/Qwen3-Embedding-0.6B` (1024 dimensions, Apache-2.0, revision pinned) through
sentence-transformers, on MPS when available, loaded once per process (about 1.2 GB), with
normalised vectors; a question is encoded with the model's `query` prompt, as its card shows.
`HashEmbedder` is a deterministic stand-in for tests and CI (no model): hashed word counts, also
1024 dimensions and normalised. The two never mix in one index: the ingest records which one
wrote the vectors in `handbook_version`.
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
from collections.abc import Sequence
from typing import Any, Final, Protocol

DIMENSIONS: Final = 1024
MODEL_ID: Final = "Qwen/Qwen3-Embedding-0.6B"
MODEL_REVISION: Final = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
_WORD: Final = re.compile(r"\w+")

_MODELS: dict[tuple[str, str], Any] = {}
_MODELS_LOCK = threading.Lock()
#: One encode at a time: the API embeds questions from worker threads, and one MPS model is shared.
_ENCODE_LOCK = threading.Lock()


class Embedder(Protocol):
    name: str

    def documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def query(self, text: str) -> list[float]: ...


class EmbeddingsUnavailableError(RuntimeError):
    """sentence-transformers is not installed (the `embeddings` extra, Apple silicon)."""


def _model(model_id: str, revision: str) -> Any:
    with _MODELS_LOCK:
        model = _MODELS.get((model_id, revision))
        if model is None:
            try:
                import torch  # type: ignore[import-not-found]
                from sentence_transformers import (  # type: ignore[import-not-found]
                    SentenceTransformer,
                )
            except ImportError as exc:
                raise EmbeddingsUnavailableError(
                    "sentence-transformers is not installed; install the rag 'embeddings' extra."
                ) from exc
            device = "mps" if torch.backends.mps.is_available() else "cpu"
            model = SentenceTransformer(model_id, revision=revision, device=device)
            _MODELS[(model_id, revision)] = model
        return model


class QwenEmbedder:
    def __init__(self, model_id: str = MODEL_ID, revision: str = MODEL_REVISION) -> None:
        self.model_id, self.revision = model_id, revision
        self.name = f"{model_id.split('/')[-1].lower()}@{revision[:8]}"

    def documents(self, texts: Sequence[str]) -> list[list[float]]:
        model = _model(self.model_id, self.revision)
        with _ENCODE_LOCK:
            vectors = model.encode(list(texts), normalize_embeddings=True, batch_size=16)
        return [[float(x) for x in vector] for vector in vectors]

    def query(self, text: str) -> list[float]:
        model = _model(self.model_id, self.revision)
        with _ENCODE_LOCK:
            vector = model.encode([text], prompt_name="query", normalize_embeddings=True)[0]
        return [float(x) for x in vector]


class HashEmbedder:
    """Deterministic, model-free vectors (tests, CI): each word adds 1 to one of 1024 buckets."""

    name = "hash@1024"

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * DIMENSIONS
        for word in _WORD.findall(text.lower()):
            bucket = int(hashlib.sha256(word.encode("utf-8")).hexdigest(), 16) % DIMENSIONS
            vector[bucket] += 1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]

    def documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def query(self, text: str) -> list[float]:
        return self._vector(text)


def vector_literal(vector: Sequence[float]) -> str:
    """A pgvector literal, `[0.1,0.2,...]`."""
    if len(vector) != DIMENSIONS:
        raise ValueError(f"expected {DIMENSIONS} dimensions, got {len(vector)}")
    return "[" + ",".join(f"{x:.7f}" for x in vector) + "]"


__all__ = [
    "DIMENSIONS",
    "MODEL_ID",
    "MODEL_REVISION",
    "Embedder",
    "EmbeddingsUnavailableError",
    "HashEmbedder",
    "QwenEmbedder",
    "vector_literal",
]
