"""`config/rag.yaml`: the retrieval and ask-the-clinic settings (M2-09)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal, cast

import yaml

from careflow_rag.agent import AskConfig
from careflow_rag.retrieve import Mode

RAG_CONFIG: Final = Path(__file__).resolve().parents[4] / "config" / "rag.yaml"
MODES: Final = ("dense", "bm25", "hybrid")
EMBEDDERS: Final = ("qwen", "hash")


@dataclass(frozen=True)
class RagConfig:
    ask: AskConfig
    mode: Mode = "dense"
    k: int = 5
    embedder: Literal["qwen", "hash"] = "qwen"
    history_turns: int = 3


def load_rag_config(path: Path = RAG_CONFIG) -> RagConfig:
    body: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    retrieval: dict[str, Any] = body.get("retrieval") or {}
    ask: dict[str, Any] = body.get("ask") or {}
    mode, embedder = retrieval.get("mode", "dense"), retrieval.get("embedder", "qwen")
    if mode not in MODES:
        raise ValueError(f"config/rag.yaml: retrieval.mode must be one of {MODES}, not {mode!r}")
    if embedder not in EMBEDDERS:
        raise ValueError(f"config/rag.yaml: retrieval.embedder must be one of {EMBEDDERS}")
    if "min_score" not in ask:
        raise ValueError("config/rag.yaml: ask.min_score is required")
    return RagConfig(
        ask=AskConfig(
            min_score=float(ask["min_score"]), llm_timeout_s=float(ask.get("llm_timeout_s", 20))
        ),
        mode=cast(Mode, mode),
        k=int(retrieval.get("k", 5)),
        embedder=cast(Literal["qwen", "hash"], embedder),
        history_turns=int(ask.get("history_turns", 3)),
    )


__all__ = ["RAG_CONFIG", "RagConfig", "load_rag_config"]
