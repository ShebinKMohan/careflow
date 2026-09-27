"""`retrieve()`: the best handbook chunks for a question, in its language (M2-08).

Three modes:

- `dense`: the 20 nearest chunks by cosine distance on the Qwen3 embedding (HNSW).
- `bm25`: the 20 best by BM25 keyword score (pg_textsearch), through the language's partial index,
  named explicitly. A chunk with no query term (score 0) is not a match and gets no rank.
- `hybrid`: both lists fused by reciprocal rank fusion, `score = sum(1 / (60 + rank))`, which uses
  ranks only, so the scales and signs of the raw scores never matter.

Every query filters by the question's language, so an English section never answers an Arabic
question by accident. The caller's session carries the clinic's row-level security scope; the
query also filters by clinic. The question is embedded in a worker thread, so a model call never
blocks the API's event loop.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from typing import Final, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from careflow_contracts import RetrievedChunk
from careflow_rag.embed import Embedder, vector_literal

type Mode = Literal["dense", "bm25", "hybrid"]
type Key = tuple[str, int]
RRF_K: Final = 60
CANDIDATES: Final = 20
BM25_INDEX: Final = {"en": "rag_bm25_en", "ar": "rag_bm25_ar"}
# One statement per language: the language is literal so the planner can use the partial BM25
# index, whose name `to_bm25query` also needs.
_DENSE: Final = {
    "en": text(
        "SELECT section_id, chunk_no, title, body, "
        "1 - (embedding <=> CAST(:q AS vector)) AS cosine FROM rag_chunks "
        "WHERE clinic_id = :clinic AND lang = 'en' "
        "ORDER BY embedding <=> CAST(:q AS vector) LIMIT :n"
    ),
    "ar": text(
        "SELECT section_id, chunk_no, title, body, "
        "1 - (embedding <=> CAST(:q AS vector)) AS cosine FROM rag_chunks "
        "WHERE clinic_id = :clinic AND lang = 'ar' "
        "ORDER BY embedding <=> CAST(:q AS vector) LIMIT :n"
    ),
}
_BM25: Final = {
    "en": text(
        "SELECT section_id, chunk_no, title, body, "
        "body <@> to_bm25query(:q, 'rag_bm25_en') AS score FROM rag_chunks "
        "WHERE clinic_id = :clinic AND lang = 'en' "
        "ORDER BY body <@> to_bm25query(:q, 'rag_bm25_en') LIMIT :n"
    ),
    "ar": text(
        "SELECT section_id, chunk_no, title, body, "
        "body <@> to_bm25query(:q, 'rag_bm25_ar') AS score FROM rag_chunks "
        "WHERE clinic_id = :clinic AND lang = 'ar' "
        "ORDER BY body <@> to_bm25query(:q, 'rag_bm25_ar') LIMIT :n"
    ),
}


def rrf(rankings: Sequence[Sequence[Key]], k: int = RRF_K) -> dict[Key, float]:
    """Reciprocal rank fusion: each list adds 1/(k + rank), ranks from 1."""
    scores: dict[Key, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking, start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    return scores


def _lang(lang: str) -> Literal["en", "ar"]:
    if lang not in BM25_INDEX:
        raise ValueError(f"unsupported language {lang!r}")
    return lang  # type: ignore[return-value]


async def retrieve(
    db: AsyncSession,
    query: str,
    lang: Literal["en", "ar"],
    clinic_id: uuid.UUID,
    *,
    embedder: Embedder,
    k: int = 5,
    mode: Mode = "hybrid",
) -> list[RetrievedChunk]:
    language = _lang(lang)
    rows: dict[Key, dict[str, object]] = {}
    dense: list[Key] = []
    keyword: list[Key] = []
    if mode in ("dense", "hybrid"):
        vector = await asyncio.to_thread(embedder.query, query)
        found = await db.execute(
            _DENSE[language],
            {"q": vector_literal(vector), "clinic": clinic_id, "n": CANDIDATES},
        )
        for row in found.mappings():
            key = (row["section_id"], row["chunk_no"])
            dense.append(key)
            rows.setdefault(key, dict(row))["cosine"] = float(row["cosine"])
    if mode in ("bm25", "hybrid"):
        found = await db.execute(
            _BM25[language],
            {"q": query, "clinic": clinic_id, "n": CANDIDATES},
        )
        for row in found.mappings():
            if row["score"] >= 0:  # no query term in the chunk: not a keyword match at all
                continue
            key = (row["section_id"], row["chunk_no"])
            keyword.append(key)
            rows.setdefault(key, dict(row))
    fused = rrf([ranking for ranking in (dense, keyword) if ranking])
    ordered = sorted(
        fused,
        key=lambda key: (
            -fused[key],
            dense.index(key) if key in dense else CANDIDATES,
            keyword.index(key) if key in keyword else CANDIDATES,
        ),
    )
    return [
        RetrievedChunk(
            section_id=key[0],
            chunk_no=key[1],
            lang=language,  # type: ignore[arg-type]
            title=str(rows[key]["title"]),
            body=str(rows[key]["body"]),
            dense_cosine=rows[key].get("cosine"),  # type: ignore[arg-type]
            bm25_rank=keyword.index(key) + 1 if key in keyword else None,
            rrf_score=round(fused[key], 6),
        )
        for key in ordered[:k]
    ]


def citation(chunk: RetrievedChunk) -> dict[str, str]:
    """The API's citation for a chunk: `doc_id` is the section, `chunk_id` is
    `<section>:<chunk>` (the API's Identifier pattern has no `#`)."""
    return {
        "doc_id": chunk.section_id,
        "title": chunk.title,
        "chunk_id": f"{chunk.section_id}:{chunk.chunk_no}",
    }


__all__ = ["BM25_INDEX", "CANDIDATES", "RRF_K", "Mode", "citation", "retrieve", "rrf"]
