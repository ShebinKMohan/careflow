"""The KTAS rows as Laya training items (M3-01).

Rows come from M1-04's loader (`ktas/load.py`) and mapping (`ktas/features.py::row_to_facts`), with
the fixed split `splits/ktas_v1.json` (seed 42): the real data is never re-split, and the leakage
columns never reach a state. One addition for a text model: the row's free-text `Chief_complain`
becomes the `chief_complaint` fact (the M1-04 mapping keeps only its category), because at serving
time the intake gives Laya the patient's own words.

Items follow the official fine-tuning notebook (NandhaKishorM/laya @ 4066d5d5): the fixed
tokenizer config, `build_sequence` with the checkpoint's `max_len`/`head_max_len`, one `choice`
question, and a one-hot target on `KTAS_expert`. Each training row is encoded twice, with all its
vitals and as the kiosk sees it (no respiratory rate, blood pressure or consciousness); each epoch
takes the kiosk version with probability 0.5.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np

from careflow_contracts import StructuredFacts
from careflow_contracts.generated.fact_schema import TextFact
from careflow_triage.ktas.features import RECORDED_AT, row_to_facts
from careflow_triage.ktas.load import file_sha256, load_ktas
from careflow_triage.laya_ktas.serialize import (
    KTAS_QUESTION,
    LEVEL_KEYS,
    LayaInput,
    facts_to_state,
)
from careflow_triage.paths import FIXTURE_CSV, SPLITS_DIR
from careflow_triage.splits import SEED, load_splits, make_splits

SPLITS_FILE: Final = SPLITS_DIR / "ktas_v1.json"
COMPLAINT_SLOT: Final = 31  # a fact-id slot row_to_facts never uses (it numbers from 1)
CHOICE: Final = 0  # laya.common.QTYPES["choice"]


@dataclass(frozen=True)
class Example:
    index: int  # the row number in the CSV
    level: int  # KTAS_expert, 1-5
    laya_input: LayaInput


@dataclass(frozen=True)
class KtasData:
    examples: list[Example]
    splits: dict[str, list[int]]
    csv_sha256: str
    fixture: bool

    def part(self, name: str) -> list[Example]:
        return [self.examples[i] for i in self.splits[name]]


def with_complaint_text(facts: StructuredFacts, row: dict[str, Any], index: int) -> StructuredFacts:
    """The facts plus the row's free-text complaint as `chief_complaint` (nurse-recorded)."""
    text = " ".join(str(row.get("Chief_complain") or "").split())
    if not text:
        return facts
    from careflow_triage.ktas.features import _fact_id  # the M1-04 fact-id scheme

    fact = TextFact.model_validate(
        {
            "fact_id": _fact_id(index, COMPLAINT_SLOT),
            "provenance": "NURSE_CONFIRMED",
            "source": {"kind": "nurse", "staff_user_id": "ktas_triage_nurse"},
            "recorded_at": RECORDED_AT,
            "value": text[:2000],
        }
    )
    return facts.model_copy(update={"chief_complaint": fact})


def load_data(csv_path: Path) -> KtasData:
    frame = load_ktas(csv_path)
    rows = frame.to_dict("records")
    examples = [
        Example(
            index=i,
            level=int(row["KTAS_expert"]),
            laya_input=facts_to_state(with_complaint_text(row_to_facts(row, index=i), row, i)),
        )
        for i, row in enumerate(rows)
    ]
    sha = file_sha256(csv_path)
    fixture = csv_path.name == FIXTURE_CSV.name
    splits = load_splits(SPLITS_FILE, csv_sha256=sha)
    if splits is None:
        if not fixture:
            raise ValueError(
                f"{SPLITS_FILE.name} was not made from this CSV (sha {sha[:8]}): run "
                "`careflow-triage train --model hgb` first; the data is never re-split here"
            )
        # The fixture (CI): its own seeded split, in memory only; the real split file stays.
        splits = make_splits(np.array([e.level for e in examples]), seed=SEED)
    return KtasData(examples=examples, splits=splits, csv_sha256=sha, fixture=fixture)


def internal_question() -> dict[str, Any]:
    """`KTAS_QUESTION` in the form `build_sequence` reads ({t, ins, crit})."""
    q = KTAS_QUESTION["urgency"]
    return {"t": q["type"], "ins": q["instructions"], "crit": dict(q["criteria"])}


def load_tokenizer(model_dir: Path) -> Any:
    """The checkpoint's tokenizer, after the notebook's config fix."""
    from laya.agent import _fix_tokenizer_config  # type: ignore[import-not-found]
    from transformers import AutoTokenizer  # type: ignore[import-not-found]

    _fix_tokenizer_config(str(model_dir))
    return AutoTokenizer.from_pretrained(str(model_dir / "tokenizer"))


def agent_config(model_dir: Path) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((model_dir / "rl_agent_config.json").read_text("utf-8"))
    return body


def encode(tok: Any, cfg: dict[str, Any], laya_input: LayaInput, level: int) -> dict[str, Any]:
    """One training item: token ids, the 5 option markers, the choice type and the target."""
    from laya.common import build_sequence  # type: ignore[import-not-found]

    ids, markers = build_sequence(
        tok, laya_input.state, internal_question(), cfg["max_len"], cfg["head_max_len"]
    )
    if len(markers) != len(LEVEL_KEYS):
        raise ValueError(f"expected {len(LEVEL_KEYS)} option markers, got {len(markers)}")
    target = [1.0 if i == level - 1 else 0.0 for i in range(len(LEVEL_KEYS))]
    return {"ids": ids, "markers": markers, "qtype": CHOICE, "target": target, "label": level - 1}


@dataclass(frozen=True)
class EncodedExample:
    level: int
    full: dict[str, Any]
    kiosk: dict[str, Any]


def encode_examples(tok: Any, cfg: dict[str, Any], examples: list[Example]) -> list[EncodedExample]:
    return [
        EncodedExample(
            level=e.level,
            full=encode(tok, cfg, e.laya_input, e.level),
            kiosk=encode(tok, cfg, e.laya_input.kiosk_partial(), e.level),
        )
        for e in examples
    ]


def epoch_items(
    encoded: list[EncodedExample], *, epoch: int, seed: int, kiosk_p: float
) -> list[dict[str, Any]]:
    """This epoch's training items: shuffled, each row as the kiosk sees it with p=`kiosk_p`."""
    rng = np.random.default_rng(seed * 1000 + epoch)
    items = [e.kiosk if rng.random() < kiosk_p else e.full for e in encoded]
    return [items[i] for i in rng.permutation(len(items))]


__all__ = [
    "EncodedExample",
    "Example",
    "KtasData",
    "agent_config",
    "encode",
    "encode_examples",
    "epoch_items",
    "internal_question",
    "load_data",
    "load_tokenizer",
    "with_complaint_text",
]
