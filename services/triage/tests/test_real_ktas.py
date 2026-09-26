"""Checks on the real KTAS data (skipped when the CSV is absent, e.g. in CI)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from careflow_triage.ktas.load import file_sha256, load_ktas
from careflow_triage.paths import KTAS_CSV, REPORTS_DIR, SPLITS_DIR
from careflow_triage.train import train

pytestmark = pytest.mark.requires_ktas


def test_the_real_table_has_the_published_shape() -> None:
    frame = load_ktas(KTAS_CSV)
    assert frame.shape == (1267, 24)
    counts = frame["KTAS_expert"].value_counts().sort_index().to_dict()
    assert counts == {1.0: 26, 2.0: 220, 3.0: 487, 4.0: 459, 5.0: 75}
    assert (
        int((frame["Sex"] == 1).sum()) == 606 and int((frame["Injury"] == 2).sum()) == 244
    )  # Table 1


def test_the_committed_split_matches_this_csv() -> None:
    body = json.loads((SPLITS_DIR / "ktas_v1.json").read_text(encoding="utf-8"))
    assert body["csv_sha256"] == file_sha256(KTAS_CSV)


def test_retraining_lr_reproduces_the_committed_report(tmp_path: Path) -> None:
    reports = sorted(REPORTS_DIR.glob("ktas_baseline_*.json"))
    assert reports, "no committed baseline report"
    committed = json.loads(reports[-1].read_text(encoding="utf-8"))["models"]["lr"]
    result = train("lr", KTAS_CSV, artifacts_dir=tmp_path, splits_path=SPLITS_DIR / "ktas_v1.json")
    assert result.meta["metrics"] == committed["metrics"]
