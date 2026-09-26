"""Loading KTAS files with their quirks, and getting the data (download chain)."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import httpx
import pytest
import respx
from openpyxl import Workbook

from careflow_triage.ktas import download as dl
from careflow_triage.ktas.load import COLUMNS, file_sha256, load_ktas, profile_markdown


def test_fixture_loads_with_quirks(fixture_csv: Path) -> None:
    frame = load_ktas(fixture_csv)
    assert frame.shape == (60, 24) and list(frame.columns) == list(COLUMNS)
    assert sorted(frame["KTAS_expert"].value_counts().to_dict().items()) == [
        (float(i), 12) for i in range(1, 6)
    ]
    assert frame["NRS_pain"].isna().to_numpy().any()  # '#NULL!'
    assert frame["Saturation"].isna().to_numpy().any()  # empty for group 1
    assert frame["BT"].between(34, 42).all()  # decimal commas became floats


def test_latin1_semicolon_csv_and_placeholders(tmp_path: Path) -> None:
    """The Kaggle CSV's reported quirks: ';', decimal commas, latin-1 text, placeholders."""
    values = {c: "1" for c in COLUMNS} | {
        "Chief_complain": "fièvre",
        "BT": "36,6",
        "Saturation": "??",
        "HR": "",
    }
    path = tmp_path / "ktas.csv"
    path.write_bytes(
        (";".join(COLUMNS) + "\n" + ";".join(values[c] for c in COLUMNS) + "\n").encode("latin-1")
    )
    frame = load_ktas(path)
    assert frame.loc[0, "BT"] == 36.6
    assert math.isnan(frame.loc[0, "Saturation"]) and math.isnan(frame.loc[0, "HR"])
    assert frame.loc[0, "Chief_complain"] == "fièvre"


def test_a_table_without_the_ktas_columns_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "other.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing columns"):
        load_ktas(path)


def test_profile_has_aggregates_only(fixture_csv: Path) -> None:
    text = profile_markdown(
        load_ktas(fixture_csv), source="fixture", sha256=file_sha256(fixture_csv)
    )
    assert (
        "| 1 | 12 |" in text and "synthetic" not in text
    )  # no row text (Diagnosis is 'synthetic')


def xlsx_bytes(tmp_path: Path) -> bytes:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(list(COLUMNS))
    sheet.append(["1" if c != "Chief_complain" else "fever" for c in COLUMNS])
    path = tmp_path / "s1.xlsx"
    book.save(path)
    return path.read_bytes()


def test_download_uses_an_existing_csv(tmp_path: Path) -> None:
    csv = tmp_path / "ktas.csv"
    csv.write_text("already here", encoding="utf-8")
    assert dl.download(csv).source == "existing"
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "doi:10.1371/journal.pone.0216972" in readme and "CC BY 4.0" in readme


@respx.mock
def test_download_fetches_and_checks_the_plos_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = xlsx_bytes(tmp_path)
    monkeypatch.setattr(dl, "PLOS_SHA256", hashlib.sha256(content).hexdigest())
    respx.get(dl.PLOS_URL).mock(return_value=httpx.Response(200, content=content))
    got = dl.download(tmp_path / "raw" / "ktas.csv", client=httpx.Client())
    assert got.source == "plos"
    frame = load_ktas(got.path)
    assert frame.shape == (1, 24) and frame.loc[0, "Chief_complain"] == "fever"


@respx.mock
def test_a_checksum_mismatch_falls_back_to_the_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx.get(dl.PLOS_URL).mock(return_value=httpx.Response(200, content=b"tampered"))
    monkeypatch.setattr(dl, "fetch_kaggle", lambda dest: None)
    got = dl.download(tmp_path / "raw" / "ktas.csv", client=httpx.Client())
    assert got.source == "fixture" and got.path.name == "ktas_fixture.csv"
    assert (tmp_path / "raw" / "README.md").exists()
    assert not (tmp_path / "raw" / "ktas.csv").exists()
