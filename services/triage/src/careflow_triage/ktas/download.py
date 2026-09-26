"""Get the KTAS data without a Kaggle login (M1-04), in this order:

(a) the CSV already in `data/raw/`; (b) the PLOS ONE S1 Appendix xlsx (the same 1,267 rows,
CC BY 4.0), checked against its SHA-256 and converted to CSV; (c) `kaggle datasets download` when
`~/.kaggle/kaggle.json` exists; (d) the synthetic fixture, whose results are NOT A RESULT.
Each run copies `DATA.md` (steps, citation, licence) to `data/README.md`, so the attribution
stays with the git-ignored data.
"""

from __future__ import annotations

import csv
import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

import httpx
from openpyxl import load_workbook

from careflow_triage.ktas.fixture import write_fixture
from careflow_triage.ktas.load import COLUMNS
from careflow_triage.paths import DATA_DIR, DATA_DOC, FIXTURE_CSV, KTAS_CSV, RAW_DIR

PLOS_URL: Final = "https://journals.plos.org/plosone/article/file?type=supplementary&id=10.1371/journal.pone.0216972.s001"
PLOS_SHA256: Final = "f631c951178799179d0fbd74fe979464b955ea1b426222bf015b163b8a24beb0"
KAGGLE_DATASET: Final = "ilkeryildiz/emergency-service-triage-application"


@dataclass(frozen=True)
class Download:
    source: Literal["existing", "plos", "kaggle", "fixture"]
    path: Path


def xlsx_to_csv(xlsx: Path, csv_path: Path) -> Path:
    sheet = load_workbook(xlsx, read_only=True, data_only=True).worksheets[0]
    rows = list(sheet.iter_rows(values_only=True))
    header = [str(c).strip() for c in rows[0]]
    if tuple(header) != COLUMNS:
        raise ValueError(f"unexpected KTAS columns: {header}")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for row in rows[1:]:
            writer.writerow(["" if v is None else v for v in row])
    return csv_path


def fetch_plos(dest: Path, *, client: httpx.Client | None = None) -> Path:
    owned = client is None
    http = client or httpx.Client(follow_redirects=True, timeout=60)
    try:
        response = http.get(PLOS_URL)
        response.raise_for_status()
    finally:
        if owned:
            http.close()
    digest = hashlib.sha256(response.content).hexdigest()
    if digest != PLOS_SHA256:
        raise ValueError(f"PLOS ONE S1 Appendix checksum mismatch: {digest}")
    xlsx = dest.parent / "ktas_plos_s1.xlsx"
    dest.parent.mkdir(parents=True, exist_ok=True)
    xlsx.write_bytes(response.content)
    return xlsx_to_csv(xlsx, dest)


def fetch_kaggle(dest: Path) -> Path | None:
    if not (Path.home() / ".kaggle" / "kaggle.json").exists() or shutil.which("kaggle") is None:
        return None
    target = dest.parent / "kaggle"
    command = ["kaggle", "datasets", "download", "-d", KAGGLE_DATASET, "-p", str(target), "--unzip"]
    subprocess.run(command, check=True, timeout=300)  # noqa: S603 - fixed arguments
    found = sorted(target.glob("*.csv"))
    if not found:
        return None
    shutil.copyfile(found[0], dest)
    return dest


def write_readme(dest: Path) -> Path:
    """Copy DATA.md next to the data: `data/README.md` for the default CSV, else beside `dest`."""
    target = DATA_DIR / "README.md" if dest == KTAS_CSV else dest.parent / "README.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DATA_DOC, target)
    return target


def download(dest: Path = KTAS_CSV, *, client: httpx.Client | None = None) -> Download:
    result = _obtain(dest, client=client)
    write_readme(dest)
    return result


def _obtain(dest: Path, *, client: httpx.Client | None) -> Download:
    if dest.exists():
        return Download("existing", dest)
    try:
        return Download("plos", fetch_plos(dest, client=client))
    except (httpx.HTTPError, ValueError, OSError):
        pass
    try:
        kaggle = fetch_kaggle(dest)
    except (subprocess.SubprocessError, OSError):
        kaggle = None
    if kaggle is not None:
        return Download("kaggle", kaggle)
    return Download(
        "fixture",
        write_fixture(FIXTURE_CSV if dest == KTAS_CSV else dest.with_name("ktas_fixture.csv")),
    )


__all__ = [
    "PLOS_SHA256",
    "PLOS_URL",
    "RAW_DIR",
    "Download",
    "download",
    "fetch_plos",
    "write_readme",
    "xlsx_to_csv",
]
