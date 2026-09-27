"""`rag_chunks` in a real Postgres (M2-08): the migration's indexes and row-level security, an
idempotent ingest, and retrieval on the fixture handbook in each mode. Run on the isolated test
stack; skipped without Postgres (conftest)."""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from careflow_contracts import RetrievedChunk
from careflow_rag.embed import EmbeddingsUnavailableError, HashEmbedder
from careflow_rag.handbook import load
from careflow_rag.ingest import ingest
from careflow_rag.retrieve import Mode, retrieve, rrf
from careflow_rag.scratch import ScratchDb

FIXTURE: Final = Path(__file__).parent / "fixtures" / "handbook"
EMBEDDER: Final = HashEmbedder()


def load_fixture(db: ScratchDb, clinic: uuid.UUID, handbook: Path = FIXTURE) -> dict[str, int]:
    with psycopg.connect(db.app_url) as conn:
        return ingest(conn, load(handbook), clinic_id=clinic, embedder=EMBEDDER)


def stored(
    db: ScratchDb, clinic: uuid.UUID, *, scope: uuid.UUID | None = None
) -> list[tuple[Any, ...]]:
    """The rows the app sees with `scope` as its clinic (default: `clinic`), for `clinic`."""
    with psycopg.connect(db.app_url) as conn, conn.transaction():
        if scope is not None or clinic is not None:
            conn.execute("SELECT set_config('app.clinic_id', %s, true)", (str(scope or clinic),))
        return conn.execute(
            "SELECT id, section_id, chunk_no, lang, content_sha256 FROM rag_chunks "
            "WHERE clinic_id = %s ORDER BY section_id, lang, chunk_no",
            (clinic,),
        ).fetchall()


async def search(
    db: ScratchDb,
    clinic: uuid.UUID,
    query: str,
    lang: str,
    mode: Mode,
    *,
    embedder: Any = EMBEDDER,
) -> list[RetrievedChunk]:
    engine = create_async_engine(db.app_url.replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        async with AsyncSession(engine) as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.clinic_id', :c, true)"), {"c": str(clinic)}
            )
            return await retrieve(session, query, lang, clinic, embedder=embedder, mode=mode)  # type: ignore[arg-type]
    finally:
        await engine.dispose()


def sections(chunks: Sequence[RetrievedChunk]) -> list[str]:
    return [chunk.section_id for chunk in chunks]


def test_the_migration_creates_rag_chunks_with_its_hnsw_and_bm25_indexes(
    scratch: ScratchDb,
) -> None:
    with psycopg.connect(scratch.migrator_url) as conn:
        rows = conn.execute(
            "SELECT i.relname, am.amname, pg_get_indexdef(i.oid), "
            "pg_get_expr(x.indpred, x.indrelid) "
            "FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid "
            "JOIN pg_class t ON t.oid = x.indrelid JOIN pg_am am ON am.oid = i.relam "
            "WHERE t.relname = 'rag_chunks'"
        ).fetchall()
        security = conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'rag_chunks'"
        ).fetchone()
        policies = conn.execute(
            "SELECT policyname FROM pg_policies WHERE tablename = 'rag_chunks'"
        ).fetchall()
    indexes = {
        name: (method, definition, predicate) for name, method, definition, predicate in rows
    }
    method, definition, _ = indexes["rag_chunks_embedding_hnsw"]
    assert method == "hnsw" and "vector_cosine_ops" in definition
    for lang, config in (("en", "english"), ("ar", "arabic")):
        method, definition, predicate = indexes[f"rag_bm25_{lang}"]
        assert method == "bm25" and config in definition
        assert predicate == f"(lang = '{lang}'::text)"
    unique = [d for _, d, _ in indexes.values() if "UNIQUE" in d]
    assert any("(clinic_id, section_id, chunk_no, lang)" in d for d in unique)
    assert security == (True, True)  # enabled and forced: the owner is bound too
    assert policies == [("clinic_isolation",)]


def test_ingest_twice_leaves_the_rows_unchanged(scratch: ScratchDb) -> None:
    clinic = scratch.add_clinic("rag-twice")
    first = load_fixture(scratch, clinic)
    before = stored(scratch, clinic)
    second = load_fixture(scratch, clinic)
    assert first == {"chunks": 8, "written": 8, "unchanged": 0, "deleted": 0}
    assert second == {"chunks": 8, "written": 0, "unchanged": 8, "deleted": 0}
    assert stored(scratch, clinic) == before  # the same rows, ids and hashes
    assert len(before) == 8


def test_ingest_rewrites_a_changed_chunk_and_deletes_a_removed_one(
    scratch: ScratchDb, tmp_path: Path
) -> None:
    clinic = scratch.add_clinic("rag-change")
    handbook = tmp_path / "handbook"
    shutil.copytree(FIXTURE, handbook)
    load_fixture(scratch, clinic, handbook)
    pharmacy = handbook / "en" / "pharmacy.md"
    pharmacy.write_text(
        pharmacy.read_text(encoding="utf-8").replace("closes at 22:00", "closes at 21:00"),
        encoding="utf-8",
    )
    (handbook / "ar" / "hours.md").write_text(
        (handbook / "ar" / "hours.md")
        .read_text(encoding="utf-8")
        .split("<!-- section: hours.emergency -->")[0],
        encoding="utf-8",
    )
    counts = load_fixture(scratch, clinic, handbook)
    assert counts == {"chunks": 7, "written": 1, "unchanged": 6, "deleted": 1}
    keys = {(row[1], row[3]) for row in stored(scratch, clinic)}
    assert ("hours.emergency", "ar") not in keys and ("hours.emergency", "en") in keys


def test_a_clinic_never_sees_another_clinics_chunks(scratch: ScratchDb) -> None:
    owner, other = scratch.add_clinic("rag-owner"), scratch.add_clinic("rag-other")
    load_fixture(scratch, owner)
    assert len(stored(scratch, owner)) == 8
    assert stored(scratch, owner, scope=other) == []  # row-level security, not the WHERE
    with psycopg.connect(scratch.app_url) as conn, conn.transaction():
        # No clinic scope at all: nothing.
        assert conn.execute("SELECT count(*) FROM rag_chunks").fetchone() == (0,)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with psycopg.connect(scratch.app_url) as conn, conn.transaction():
            conn.execute("SELECT set_config('app.clinic_id', %s, true)", (str(other),))
            conn.execute(
                "INSERT INTO rag_chunks (clinic_id, section_id, chunk_no, lang, title, body, "
                "embedding, content_sha256, handbook_version) "
                "VALUES (%s, 'x.y', 0, 'en', 't', 'b', %s::vector, 's', 'v')",
                (owner, "[" + ",".join(["0"] * 1023 + ["1"]) + "]"),
            )


async def test_retrieval_is_scoped_to_the_callers_clinic(scratch: ScratchDb) -> None:
    owner, other = scratch.add_clinic("rag-scope-a"), scratch.add_clinic("rag-scope-b")
    load_fixture(scratch, owner)
    assert await search(scratch, owner, "pharmacy hours", "en", "hybrid")
    assert await search(scratch, other, "pharmacy hours", "en", "hybrid") == []


@pytest.mark.parametrize("mode", ["dense", "bm25", "hybrid"])
async def test_each_mode_finds_the_right_section_in_its_language(
    scratch: ScratchDb, mode: Mode
) -> None:
    clinic = scratch.add_clinic(f"rag-{mode}")
    load_fixture(scratch, clinic)
    english = await search(scratch, clinic, "When does the pharmacy close?", "en", mode)
    arabic = await search(scratch, clinic, "متى تغلق الصيدلية؟", "ar", mode)
    assert sections(english)[0] == "pharmacy.hours"
    assert sections(arabic)[0] == "pharmacy.hours"
    assert {c.lang for c in english} == {"en"} and {c.lang for c in arabic} == {"ar"}


async def test_hybrid_scores_are_the_rrf_of_the_dense_and_bm25_ranks(scratch: ScratchDb) -> None:
    clinic = scratch.add_clinic("rag-rrf")
    load_fixture(scratch, clinic)
    query = "collect the prescription at the pharmacy counter"
    dense = await search(scratch, clinic, query, "en", "dense")
    keyword = await search(scratch, clinic, query, "en", "bm25")
    hybrid = await search(scratch, clinic, query, "en", "hybrid")
    expected = rrf(
        [[(c.section_id, c.chunk_no) for c in dense], [(c.section_id, c.chunk_no) for c in keyword]]
    )
    for chunk in hybrid:
        assert chunk.rrf_score == pytest.approx(
            expected[(chunk.section_id, chunk.chunk_no)], abs=1e-6
        )
        assert chunk.dense_cosine is not None  # every fixture chunk is in the dense top 20
    # Ordered by score; an equal score goes to the better dense rank, then the better BM25 rank.
    dense_keys = [(c.section_id, c.chunk_no) for c in dense]
    keyword_keys = [(c.section_id, c.chunk_no) for c in keyword]

    def order(chunk: RetrievedChunk) -> tuple[float, int, int]:
        key = (chunk.section_id, chunk.chunk_no)
        return (
            -expected[key],
            dense_keys.index(key) if key in dense_keys else 20,
            keyword_keys.index(key) if key in keyword_keys else 20,
        )

    assert hybrid == sorted(hybrid, key=order)
    assert keyword[0].section_id == "pharmacy.prescriptions"
    assert [c.bm25_rank for c in hybrid if c.section_id == "pharmacy.prescriptions"] == [1]


async def test_a_question_with_no_keyword_match_has_no_bm25_result(scratch: ScratchDb) -> None:
    """Zero results stay zero: a chunk without any query term is not a keyword match, so BM25
    returns nothing and hybrid is the dense ranking alone."""
    clinic = scratch.add_clinic("rag-zero")
    load_fixture(scratch, clinic)
    assert await search(scratch, clinic, "zebra quantum", "en", "bm25") == []
    hybrid = await search(scratch, clinic, "zebra quantum", "en", "hybrid")
    dense = await search(scratch, clinic, "zebra quantum", "en", "dense")
    assert sections(hybrid) == sections(dense)
    assert {c.bm25_rank for c in hybrid} == {None}


class Unavailable:
    name = "unavailable"

    def documents(self, texts: Sequence[str]) -> list[list[float]]:
        raise EmbeddingsUnavailableError("no model (test)")

    def query(self, text: str) -> list[float]:
        raise EmbeddingsUnavailableError("no model (test)")


async def test_without_embeddings_dense_and_hybrid_raise_and_bm25_still_works(
    scratch: ScratchDb,
) -> None:
    """The caller (M2-09's agent) turns the error into "I don't know"; BM25 needs no model."""
    clinic = scratch.add_clinic("rag-nomodel")
    load_fixture(scratch, clinic)
    for mode in ("dense", "hybrid"):
        with pytest.raises(EmbeddingsUnavailableError):
            await search(scratch, clinic, "pharmacy hours", "en", mode, embedder=Unavailable())  # type: ignore[arg-type]
    keyword = await search(scratch, clinic, "pharmacy hours", "en", "bm25", embedder=Unavailable())
    assert sections(keyword)[0] == "pharmacy.hours"
