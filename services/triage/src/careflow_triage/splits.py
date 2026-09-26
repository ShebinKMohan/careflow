"""Fixed, seeded, stratified 70/15/15 splits (row indices only; saved as JSON)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

import numpy as np
from sklearn.model_selection import train_test_split

SEED: Final = 42


def make_splits(labels: np.ndarray, *, seed: int = SEED) -> dict[str, list[int]]:
    """Stratified on the label: train 70%, then the rest halved into val and test."""
    index = np.arange(len(labels))
    train, rest = train_test_split(index, test_size=0.30, stratify=labels, random_state=seed)
    val, test = train_test_split(rest, test_size=0.50, stratify=labels[rest], random_state=seed)
    return {
        name: sorted(int(i) for i in part)
        for name, part in (("train", train), ("val", val), ("test", test))
    }


def save_splits(
    path: Path, splits: dict[str, list[int]], *, csv_sha256: str, seed: int, labels: np.ndarray
) -> None:
    counts = {
        name: {str(level): int((labels[idx] == level).sum()) for level in range(1, 6)}
        for name, idx in splits.items()
    }
    body = {
        "csv_sha256": csv_sha256,
        "seed": seed,
        "stratify": "KTAS_expert",
        "counts": counts,
        **splits,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")


def load_splits(path: Path, *, csv_sha256: str) -> dict[str, list[int]] | None:
    """The saved splits when they belong to this CSV, else None (they are recomputed)."""
    if not path.exists():
        return None
    body = json.loads(path.read_text(encoding="utf-8"))
    if body.get("csv_sha256") != csv_sha256:
        return None
    return {name: [int(i) for i in body[name]] for name in ("train", "val", "test")}
