"""Fixtures: the synthetic KTAS-format CSV and both baselines trained on it (in temp folders)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from careflow_triage.ktas.fixture import write_fixture
from careflow_triage.paths import KTAS_CSV
from careflow_triage.train import TrainResult, train


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if KTAS_CSV.exists():
        return
    skip = pytest.mark.skip(
        reason="requires_ktas: no KTAS CSV (run `careflow-triage ktas download`)"
    )
    for item in items:
        if "requires_ktas" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def fixture_csv(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_fixture(tmp_path_factory.mktemp("data") / "ktas_fixture.csv")


@dataclass(frozen=True)
class Trained:
    lr: TrainResult
    hgb: TrainResult
    artifacts: Path
    splits: Path


@pytest.fixture(scope="session")
def trained(tmp_path_factory: pytest.TempPathFactory, fixture_csv: Path) -> Trained:
    artifacts = tmp_path_factory.mktemp("artifacts")
    splits = tmp_path_factory.mktemp("splits") / "ktas_fixture.json"
    lr = train("lr", fixture_csv, artifacts_dir=artifacts, splits_path=splits)
    hgb = train("hgb", fixture_csv, artifacts_dir=artifacts, splits_path=splits)
    return Trained(lr, hgb, artifacts, splits)
