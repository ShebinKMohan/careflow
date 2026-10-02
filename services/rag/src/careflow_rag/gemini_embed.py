"""Hosted embedding port, explicitly selected; never mixes with the local Qwen index."""

from __future__ import annotations

import math
from collections.abc import Sequence

import httpx

from careflow_providers.adapters.gemini import BASE_URL, api_key
from careflow_providers.errors import ProviderUnavailable
from careflow_rag.embed import DIMENSIONS


class GeminiEmbedder:
    model = "gemini-embedding-001"
    name = f"{model}@{DIMENSIONS}"

    def _embed(self, texts: Sequence[str], task: str) -> list[list[float]]:
        if not texts:
            return []
        with httpx.Client(timeout=20) as client:
            response = client.post(
                f"{BASE_URL}/models/{self.model}:batchEmbedContents",
                headers={"x-goog-api-key": api_key("embedding")},
                json={
                    "requests": [
                        {
                            "model": f"models/{self.model}",
                            "content": {"parts": [{"text": text}]},
                            "taskType": task,
                            "outputDimensionality": DIMENSIONS,
                        }
                        for text in texts
                    ]
                },
            )
        if response.is_error:
            raise ProviderUnavailable("embedding", f"Gemini HTTP {response.status_code}")
        vectors = [e["values"] for e in response.json().get("embeddings", [])]
        if len(vectors) != len(texts):
            raise ValueError("Gemini embedding count mismatch")
        normalized = []
        for vector in vectors:
            if len(vector) != DIMENSIONS or not all(math.isfinite(x) for x in vector):
                raise ValueError("Invalid Gemini embedding")
            norm = math.sqrt(sum(x * x for x in vector))
            if norm == 0:
                raise ValueError("Empty Gemini embedding")
            normalized.append([x / norm for x in vector])
        return normalized

    def documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed(texts, "RETRIEVAL_DOCUMENT")

    def query(self, text: str) -> list[float]:
        return self._embed([text], "RETRIEVAL_QUERY")[0]
