"""`python -m careflow_rag.ingest <handbook_dir> --clinic <clinic_id>`: load the handbook (M2-08).

Parses and chunks the handbook, embeds only the chunks whose content hash changed, upserts them on
`(clinic_id, section_id, chunk_no, lang)`, and deletes chunks that no longer exist, so running it
twice changes nothing. It connects as the app (`DATABASE_URL`, from the environment or `.env`) with
the clinic's row-level security scope. The handbook is fictional: no patient data.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Final

import psycopg

from careflow_rag.embed import Embedder, HashEmbedder, QwenEmbedder, vector_literal
from careflow_rag.handbook import Chunk, handbook_version, load

REPO: Final = Path(__file__).resolve().parents[4]
DEFAULT_HANDBOOK: Final = REPO / "services" / "rag" / "handbook"

UPSERT: Final = """
INSERT INTO rag_chunks (
    clinic_id, section_id, chunk_no, lang, title, body, embedding, content_sha256,
    handbook_version
) VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s, %s)
ON CONFLICT (clinic_id, section_id, chunk_no, lang) DO UPDATE SET
    title = EXCLUDED.title,
    body = EXCLUDED.body,
    embedding = EXCLUDED.embedding,
    content_sha256 = EXCLUDED.content_sha256,
    handbook_version = EXCLUDED.handbook_version
"""


def database_url() -> str:
    """`DATABASE_URL` from the environment, else from the repo's `.env` (never printed)."""
    url = os.environ.get("DATABASE_URL")
    env = REPO / ".env"
    if not url and env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip()
    if not url:
        raise SystemExit("ingest: DATABASE_URL is not set")
    return url.replace("postgresql+psycopg://", "postgresql://")


def ingest(
    conn: psycopg.Connection,
    chunks: Sequence[Chunk],
    *,
    clinic_id: uuid.UUID,
    embedder: Embedder,
) -> dict[str, int]:
    """Bring the clinic's chunks in line with `chunks`, in one transaction; the counts done."""
    version = f"{handbook_version(list(chunks))}/{embedder.name}"
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SELECT set_config('app.clinic_id', %s, true)", (str(clinic_id),))
        cur.execute(
            "SELECT section_id, chunk_no, lang, content_sha256, handbook_version "
            "FROM rag_chunks WHERE clinic_id = %s",
            (clinic_id,),
        )
        existing = {(row[0], row[1], row[2]): (row[3], row[4]) for row in cur.fetchall()}
        same_embedder = all(v.endswith(f"/{embedder.name}") for _, v in existing.values())
        changed = [
            chunk
            for chunk in chunks
            if not same_embedder or existing.get(chunk.key, ("", ""))[0] != chunk.sha256
        ]
        vectors = embedder.documents([f"{c.title}\n\n{c.body}" for c in changed])
        for chunk, vector in zip(changed, vectors, strict=True):
            cur.execute(
                UPSERT,
                (
                    clinic_id,
                    chunk.section_id,
                    chunk.chunk_no,
                    chunk.lang,
                    chunk.title,
                    chunk.body,
                    vector_literal(vector),
                    chunk.sha256,
                    version,
                ),
            )
        current = {chunk.key for chunk in chunks}
        stale = [key for key in existing if key not in current]
        for section_id, chunk_no, lang in stale:
            cur.execute(
                "DELETE FROM rag_chunks WHERE clinic_id = %s AND section_id = %s "
                "AND chunk_no = %s AND lang = %s",
                (clinic_id, section_id, chunk_no, lang),
            )
        cur.execute(
            "UPDATE rag_chunks SET handbook_version = %s WHERE clinic_id = %s", (version, clinic_id)
        )
    return {
        "chunks": len(chunks),
        "written": len(changed),
        "unchanged": len(chunks) - len(changed),
        "deleted": len(stale),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("handbook", nargs="?", type=Path, default=DEFAULT_HANDBOOK)
    parser.add_argument("--clinic", required=True, type=uuid.UUID)
    parser.add_argument(
        "--embedder",
        choices=("qwen", "hash"),
        default="qwen",
        help="qwen: Qwen3-Embedding-0.6B (the default); hash: a model-free stand-in for tests",
    )
    args = parser.parse_args(argv)
    chunks = load(args.handbook)
    embedder: Embedder = QwenEmbedder() if args.embedder == "qwen" else HashEmbedder()
    with psycopg.connect(database_url()) as conn:
        counts = ingest(conn, chunks, clinic_id=args.clinic, embedder=embedder)
    print("ingest: " + ", ".join(f"{name} {value}" for name, value in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
