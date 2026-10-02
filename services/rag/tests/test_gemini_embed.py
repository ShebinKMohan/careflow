"""The hosted index uses normalized, finite 1024-dimensional retrieval embeddings."""

import httpx
import pytest
import respx

from careflow_providers.adapters.gemini import BASE_URL
from careflow_rag.gemini_embed import GeminiEmbedder


@respx.mock
def test_query_normalizes_and_selects_retrieval_task(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-server-secret")
    route = respx.post(f"{BASE_URL}/models/gemini-embedding-001:batchEmbedContents").mock(
        return_value=httpx.Response(200, json={"embeddings": [{"values": [3, 4] + [0] * 1022}]})
    )
    vector = GeminiEmbedder().query("Where is reception?")
    assert vector[:2] == [0.6, 0.8]
    assert len(vector) == 1024
    assert b"RETRIEVAL_QUERY" in route.calls[0].request.content
    assert "test-server-secret" not in str(route.calls[0].request.url)


@pytest.mark.parametrize("vector", [[0] * 1024, [1] * 1023, [float("inf")] * 1024])
@respx.mock
def test_invalid_vector_is_rejected(vector: list[float], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-server-secret")
    # JSON cannot represent infinity: use the HTTP parser's supported NaN/Infinity syntax here.
    import json

    respx.post(f"{BASE_URL}/models/gemini-embedding-001:batchEmbedContents").mock(
        return_value=httpx.Response(200, text=json.dumps({"embeddings": [{"values": vector}]}))
    )
    with pytest.raises(ValueError):
        GeminiEmbedder().query("synthetic question")
