"""Load the KTAS table (the PLOS ONE S1 Appendix as CSV, or the Kaggle CSV).

Checked quirks (M1-04): the PLOS sheet stores vitals as text and marks missing values with
'측불' (Korean for "unmeasurable") and '#NULL!'; SpO2 is empty for the local ED. The Kaggle CSV
reportedly uses ';' separators, decimal commas and a non-UTF-8 encoding. All are handled here.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd

COLUMNS: Final = (
    "Group", "Sex", "Age", "Patients number per hour", "Arrival mode", "Injury", "Chief_complain",
    "Mental", "Pain", "NRS_pain", "SBP", "DBP", "HR", "RR", "BT", "Saturation", "KTAS_RN",
    "Diagnosis in ED", "Disposition", "KTAS_expert", "Error_group", "Length of stay_min",
    "KTAS duration_min", "mistriage",
)  # fmt: skip
TEXT_COLUMNS: Final = frozenset({"Chief_complain", "Diagnosis in ED"})
#: Values that mean "not recorded" in either source.
MISSING: Final = frozenset({"", "#NULL!", "측불", "??", "NA", "N/A", "nan", "None"})


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp949", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("undecodable file")  # pragma: no cover - latin-1 decodes any byte


def _number(value: str) -> float:
    text = value.strip()
    if text in MISSING:
        return float("nan")
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return float("nan")


def load_ktas(path: Path) -> pd.DataFrame:
    """The 24 KTAS columns; numeric columns as float (NaN when not recorded), text as str."""
    text = _decode(path.read_bytes())
    header = text.splitlines()[0] if text else ""
    sep = ";" if header.count(";") > header.count(",") else ","
    frame = pd.read_csv(io.StringIO(text), sep=sep, dtype=str, keep_default_na=False)
    frame.columns = [str(c).strip() for c in frame.columns]
    missing = [c for c in COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"not a KTAS table; missing columns: {missing}")
    out = pd.DataFrame(index=frame.index)
    for column in COLUMNS:
        values = frame[column].astype(str)
        if column in TEXT_COLUMNS:
            out[column] = values.str.strip()
        else:
            out[column] = np.array([_number(v) for v in values], dtype=float)
    return out


def profile_markdown(frame: pd.DataFrame, *, source: str, sha256: str) -> str:
    """Aggregate counts only (never rows): levels and missing values per feature column."""
    lines = [
        "# KTAS dataset profile",
        "",
        f"Source: {source}. Rows: {len(frame)}. SHA-256 of the CSV: `{sha256}`.",
        "Aggregates only; the rows stay in the git-ignored `services/triage/data/`.",
        "",
        "| Level | KTAS_expert | KTAS_RN |",
        "|---|---|---|",
    ]
    for level in range(1, 6):
        expert = int((frame["KTAS_expert"] == level).sum())
        nurse = int((frame["KTAS_RN"] == level).sum())
        lines.append(f"| {level} | {expert} | {nurse} |")
    lines += ["", "| Column | Not recorded |", "|---|---|"]
    for column in (
        "Age",
        "Sex",
        "Arrival mode",
        "Injury",
        "Mental",
        "Pain",
        "NRS_pain",
        "SBP",
        "DBP",
        "HR",
        "RR",
        "BT",
        "Saturation",
    ):
        lines.append(f"| {column} | {int(frame[column].isna().to_numpy().sum())} |")
    empty = int((frame["Chief_complain"] == "").sum())
    lines.append(f"| Chief_complain | {empty} |")
    return "\n".join(lines) + "\n"
