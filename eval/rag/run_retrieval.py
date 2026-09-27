"""The retrieval eval (M2-08): hit@5 and MRR per language and mode, on labelled questions.

    python3 scripts/stack.py run --stack careflow-test -- \
        uv run --package careflow-rag --extra embeddings python eval/rag/run_retrieval.py

It creates a throwaway database (`careflow_rag_eval`, migrated like the API's; never the dev
database), ingests the handbook (services/rag/handbook) for a new clinic with the pinned
Qwen3-Embedding-0.6B, asks every question of `eval/datasets/rag/retrieval.jsonl` in each mode
(`dense`, `bm25`, `hybrid`), and drops the database. It writes `eval/reports/rag_retrieval.json`
and `.md`:

- **hit@5**: an answerable question counts when any of its gold sections is in the top 5.
- **hit@1**: the same for the first result.
- **MRR**: the mean of 1 / rank of the first gold section in the top 20 (0 when none is there).
- **The dense cosine distribution** of the best chunk, for top-1 hits, top-1 misses and the
  out-of-scope questions. This is what "Ask the clinic" (M2-09) compares with its "I don't
  know" threshold (`config/rag.yaml` `ask.min_score`), and what that threshold is chosen from.

Hybrid hit@5 >= 0.90 is the issue's target, reported as met or not; it is never a claim beyond
these questions. The questions are the agent's own (eval/datasets/rag/README.md), not real
users'. Timings are measured on this machine and are not gates.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import importlib.metadata
import json
import statistics
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Final

import psycopg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from careflow_rag.config import load_rag_config
from careflow_rag.embed import MODEL_ID, MODEL_REVISION, QwenEmbedder
from careflow_rag.handbook import handbook_version, load
from careflow_rag.ingest import DEFAULT_HANDBOOK, ingest
from careflow_rag.retrieve import CANDIDATES, RRF_K, Mode, retrieve
from careflow_rag.scratch import ScratchDb, scratch_database

REPO: Final = Path(__file__).resolve().parents[2]
DATASET: Final = REPO / "eval" / "datasets" / "rag" / "retrieval.jsonl"
REPORT_JSON: Final = REPO / "eval" / "reports" / "rag_retrieval.json"
REPORT_MD: Final = REPO / "eval" / "reports" / "rag_retrieval.md"
EVAL_DB: Final = "careflow_rag_eval"
MODES: Final[tuple[Mode, ...]] = ("dense", "bm25", "hybrid")
LANGS: Final = ("en", "ar")
K: Final = 5
TARGET: Final = 0.90  # hybrid hit@5: the issue's target, not a claim


def load_questions(path: Path = DATASET) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise SystemExit("run_retrieval: a question id is used twice")
    return rows


def first_gold_rank(found: list[str], gold: list[str]) -> int | None:
    """The 1-based rank of the first gold section in `found`, or None."""
    for rank, section in enumerate(found, start=1):
        if section in gold:
            return rank
    return None


def stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "min": round(ordered[0], 3),
        "median": round(statistics.median(ordered), 3),
        "max": round(ordered[-1], 3),
        "values": [round(v, 3) for v in ordered],
    }


def version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


async def ask_all(
    db: ScratchDb, clinic: uuid.UUID, embedder: QwenEmbedder, questions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    engine = create_async_engine(db.app_url.replace("postgresql://", "postgresql+psycopg://", 1))
    answers: list[dict[str, Any]] = []
    try:
        for question in questions:
            for mode in MODES:
                async with AsyncSession(engine) as session, session.begin():
                    await session.execute(
                        text("SELECT set_config('app.clinic_id', :c, true)"), {"c": str(clinic)}
                    )
                    started = time.perf_counter()
                    chunks = await retrieve(
                        session,
                        question["question"],
                        question["lang"],
                        clinic,
                        embedder=embedder,
                        k=CANDIDATES,
                        mode=mode,
                    )
                    elapsed = (time.perf_counter() - started) * 1000
                found = [chunk.section_id for chunk in chunks]
                cosines = [c.dense_cosine for c in chunks[:K] if c.dense_cosine is not None]
                answers.append(
                    {
                        "id": question["id"],
                        "lang": question["lang"],
                        "mode": mode,
                        "gold": question["gold_section_ids"],
                        "top5": found[:K],
                        "rank": first_gold_rank(found, question["gold_section_ids"]),
                        "top_cosine": max(cosines) if cosines else None,
                        "ms": round(elapsed, 1),
                    }
                )
    finally:
        await engine.dispose()
    return answers


def score(answers: list[dict[str, Any]], min_score: float) -> dict[str, Any]:
    results: dict[str, Any] = {}
    cosine: dict[str, Any] = {}
    for lang in LANGS:
        results[lang] = {}
        for mode in MODES:
            rows = [a for a in answers if a["lang"] == lang and a["mode"] == mode and a["gold"]]
            ranks = [a["rank"] for a in rows]
            results[lang][mode] = {
                "n": len(rows),
                "hit_at_5": round(sum(1 for r in ranks if r is not None and r <= K) / len(rows), 3),
                "hit_at_1": round(sum(1 for r in ranks if r == 1) / len(rows), 3),
                "mrr": round(sum(1 / r for r in ranks if r is not None) / len(rows), 3),
                "missed_at_5": [a["id"] for a in rows if a["rank"] is None or a["rank"] > K],
                "median_ms": round(statistics.median(a["ms"] for a in rows), 1),
            }
        dense = [a for a in answers if a["lang"] == lang and a["mode"] == "dense"]
        hits = [a["top_cosine"] for a in dense if a["gold"] and a["rank"] == 1]
        misses = [a["top_cosine"] for a in dense if a["gold"] and a["rank"] != 1]
        outside = [a["top_cosine"] for a in dense if not a["gold"]]
        answerable = [a["top_cosine"] for a in dense if a["gold"]]
        cosine[lang] = {
            "top1_hit": stats(hits),
            "top1_miss": stats(misses),
            "out_of_scope": stats(outside),
            f"answerable_below_{min_score}": sum(1 for c in answerable if c < min_score),
            f"out_of_scope_at_or_above_{min_score}": sum(1 for c in outside if c >= min_score),
        }
    hybrid = [results[lang]["hybrid"]["hit_at_5"] for lang in LANGS]
    return {
        "results": results,
        "dense_top_cosine": cosine,
        "target": {
            "hybrid_hit_at_5": TARGET,
            "met": all(value >= TARGET for value in hybrid),
            "note": "a target on these questions, not a claim",
        },
    }


async def run() -> dict[str, Any]:
    questions = load_questions()
    chunks = load(DEFAULT_HANDBOOK)
    embedder = QwenEmbedder()
    rag = load_rag_config()
    with scratch_database(EVAL_DB) as db:
        clinic = db.add_clinic("rag-eval")
        started = time.perf_counter()
        with psycopg.connect(db.app_url) as conn:
            counts = ingest(conn, chunks, clinic_id=clinic, embedder=embedder)
        ingest_s = round(time.perf_counter() - started, 1)
        with psycopg.connect(db.migrator_url) as conn:
            extensions = dict(
                conn.execute(
                    "SELECT extname, extversion FROM pg_extension "
                    "WHERE extname IN ('vector', 'pg_textsearch')"
                ).fetchall()
            )
        answers = await ask_all(db, clinic, embedder, questions)
    import torch  # the embeddings extra; loaded by QwenEmbedder already

    scored = score(answers, rag.ask.min_score)
    return {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "dataset": {
            "path": str(DATASET.relative_to(REPO)),
            "sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(),
            "answerable": {
                lang: sum(1 for q in questions if q["lang"] == lang and q["gold_section_ids"])
                for lang in LANGS
            },
            "out_of_scope": {
                lang: sum(1 for q in questions if q["lang"] == lang and not q["gold_section_ids"])
                for lang in LANGS
            },
            "written_by": "agent:claude-code/claude-opus-5-5 (not real users' questions)",
        },
        "handbook": {
            "version": handbook_version(chunks),
            "chunks": counts["chunks"],
            "sections_per_language": len({c.section_id for c in chunks}),
            "ingest_seconds": ingest_s,
        },
        "embedder": {
            "model": MODEL_ID,
            "revision": MODEL_REVISION,
            "device": "mps" if torch.backends.mps.is_available() else "cpu",
            "sentence_transformers": version("sentence-transformers"),
            "transformers": version("transformers"),
            "torch": version("torch"),
        },
        "postgres_extensions": extensions,
        "settings": {
            "k": K,
            "candidates": CANDIDATES,
            "rrf_k": RRF_K,
            "mrr_depth": CANDIDATES,
            "configured_mode": rag.mode,
            "configured_min_score": rag.ask.min_score,
        },
        **scored,
        "answers": answers,
    }


def markdown(report: dict[str, Any]) -> str:
    settings = report["settings"]
    min_score = settings["configured_min_score"]
    embedder = report["embedder"]
    lines = [
        "# Retrieval eval (M2-08)",
        "",
        "The clinic handbook (fictional, services/rag/handbook), retrieved for labelled questions "
        "in English and Arabic. Research prototype; synthetic data only.",
        "",
        f"- Generated {report['generated_at']}; dataset `{report['dataset']['path']}` "
        f"(sha256 `{report['dataset']['sha256'][:16]}…`): "
        f"{report['dataset']['answerable']['en']} + {report['dataset']['answerable']['ar']} "
        "answerable questions (English + Arabic) and "
        f"{report['dataset']['out_of_scope']['en']} + {report['dataset']['out_of_scope']['ar']} "
        "out of scope, written by the coding agent (not real users).",
        f"- Handbook version `{report['handbook']['version']}`: "
        f"{report['handbook']['sections_per_language']} sections per language, "
        f"{report['handbook']['chunks']} chunks.",
        f"- Embedder `{embedder['model']}@{embedder['revision'][:8]}` on {embedder['device']} "
        f"(sentence-transformers {embedder['sentence_transformers']}, transformers "
        f"{embedder['transformers']}, torch {embedder['torch']}); pgvector "
        f"{report['postgres_extensions'].get('vector')}, pg_textsearch "
        f"{report['postgres_extensions'].get('pg_textsearch')}.",
        f"- hit@5: a gold section in the top 5. hit@1: first. MRR: 1 / rank of the first gold "
        f"section in the top {settings['mrr_depth']}. RRF k = {settings['rrf_k']}.",
        "",
        "| Language | Mode | hit@5 | hit@1 | MRR | Missed at 5 | Median time |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for lang, modes in report["results"].items():
        for mode, row in modes.items():
            missed = ", ".join(f"`{m}`" for m in row["missed_at_5"]) or "none"
            lines.append(
                f"| {lang} | {mode} | {row['hit_at_5']:.3f} | {row['hit_at_1']:.3f} | "
                f"{row['mrr']:.3f} | {missed} | {row['median_ms']:.1f} ms |"
            )
    target = report["target"]
    lines += [
        "",
        f"Target: hybrid hit@5 >= {target['hybrid_hit_at_5']:.2f} in both languages, "
        f"{'met' if target['met'] else 'not met'} ({target['note']}).",
        "",
        "## The best chunk's dense cosine",
        "",
        f"For M2-09's \"I don't know\" threshold (configured `min_score` {min_score}, mode "
        f"`{settings['configured_mode']}`): the best dense cosine among the top 5, in dense mode.",
        "",
        "| Language | Top-1 hits | Top-1 misses | Out of scope | Answerable below "
        f"{min_score} | Out of scope at or above {min_score} |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for lang, row in report["dense_top_cosine"].items():
        cells = [lang] + [_range(row[name]) for name in ("top1_hit", "top1_miss", "out_of_scope")]
        cells += [
            str(row[f"answerable_below_{min_score}"]),
            str(row[f"out_of_scope_at_or_above_{min_score}"]),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines += [
        "",
        "Cells: n; min / median / max. A small corpus (25 sections per language): every chunk "
        "is in the dense top 20, so these numbers say little about a larger handbook.",
    ]
    return "\n".join(lines) + "\n"


def _range(row: dict[str, Any]) -> str:
    if not row["n"]:
        return "0"
    return f"{row['n']}; {row['min']:.3f} / {row['median']:.3f} / {row['max']:.3f}"


def main() -> int:
    report = asyncio.run(run())
    REPORT_JSON.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    REPORT_MD.write_text(markdown(report), encoding="utf-8")
    print(markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
