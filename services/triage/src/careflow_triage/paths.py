"""Where the triage pipeline reads and writes (a workspace checkout)."""

from __future__ import annotations

from pathlib import Path
from typing import Final

SERVICE_DIR: Final = Path(__file__).resolve().parents[2]  # services/triage
REPO_ROOT: Final = SERVICE_DIR.parents[1]
CONFIG_DIR: Final = SERVICE_DIR / "config"
DATA_DIR: Final = SERVICE_DIR / "data"  # git-ignored: rows are never committed
RAW_DIR: Final = DATA_DIR / "raw"
KTAS_CSV: Final = RAW_DIR / "ktas.csv"
FIXTURE_CSV: Final = RAW_DIR / "ktas_fixture.csv"
ARTIFACTS_DIR: Final = SERVICE_DIR / "artifacts"  # git-ignored
SPLITS_DIR: Final = SERVICE_DIR / "splits"
REPORTS_DIR: Final = REPO_ROOT / "eval" / "reports" / "triage"
MODEL_CARD: Final = SERVICE_DIR / "MODEL_CARD.md"
DATA_DOC: Final = SERVICE_DIR / "DATA.md"  # committed; copied to data/README.md by `ktas download`
