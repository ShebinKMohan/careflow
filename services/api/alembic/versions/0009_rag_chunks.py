"""The clinic handbook for retrieval (M2-08): rag_chunks with dense and keyword indexes.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-27

One row per handbook chunk and language: the section id (the same in every language), the chunk
number, the title and body, a 1024-dimension Qwen3-Embedding vector, and the content hash the
ingest skips unchanged chunks by. Indexes: HNSW on the embedding (cosine), and one BM25 index
(pg_textsearch) per language, partial on `lang` with the matching text search configuration
(`english`, `arabic`), named so the queries can address them. The handbook is fictional: no patient
data ever enters this table. Clinic-scoped like 0001 (ENABLE + FORCE ROW LEVEL SECURITY and the
clinic_isolation policy); the app reads, and the ingest writes and removes chunks.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_CLINIC = "NULLIF(current_setting('app.clinic_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE rag_chunks (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            clinic_id uuid NOT NULL REFERENCES clinic (id),
            section_id text NOT NULL CHECK (char_length(section_id) <= 120),
            chunk_no integer NOT NULL CHECK (chunk_no >= 0),
            lang text NOT NULL CHECK (lang IN ('en', 'ar')),
            title text NOT NULL,
            body text NOT NULL,
            embedding vector(1024) NOT NULL,
            content_sha256 text NOT NULL,
            handbook_version text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (clinic_id, section_id, chunk_no, lang)
        );
        CREATE INDEX ix_rag_chunks_clinic_id ON rag_chunks (clinic_id);
        CREATE INDEX rag_chunks_embedding_hnsw ON rag_chunks
            USING hnsw (embedding vector_cosine_ops);
        CREATE INDEX rag_bm25_en ON rag_chunks
            USING bm25 (body) WITH (text_config = 'english') WHERE lang = 'en';
        CREATE INDEX rag_bm25_ar ON rag_chunks
            USING bm25 (body) WITH (text_config = 'arabic') WHERE lang = 'ar';
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON rag_chunks TO careflow_app")
    op.execute("ALTER TABLE rag_chunks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE rag_chunks FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY clinic_isolation ON rag_chunks "
        f"USING (clinic_id = {CURRENT_CLINIC}) WITH CHECK (clinic_id = {CURRENT_CLINIC})"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rag_chunks")
