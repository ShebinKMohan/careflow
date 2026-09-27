"""The handbook's automated review (M2-08): every file passed, and the recorded review still
covers the files as they are now (no model: `python -m careflow_rag.review --check`)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from careflow_rag.review import HANDBOOK, REPORT, check, deterministic_checks, handbook_files


def test_every_handbook_file_passed_the_recorded_automated_review() -> None:
    assert check() == []
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert report["reviewed_by"].startswith("agent:local/")
    assert report["written_by"] != report["reviewed_by"]  # a different model judged the writer
    assert sorted(report["files"]) == handbook_files() and len(report["files"]) == 8
    for item in report["files"].values():
        assert item["passed"] and item["same_sections"] and not item["denylist_hits"]
        assert all(b["passed"] and b["chrf"] >= 40 for b in item["backtranslation"])
        judge = item["judge"]
        assert judge["fictional_only"] and judge["practical_only"]
        assert judge["arabic_matches_english"]


def test_the_review_check_fails_when_a_file_changed_since_the_review(tmp_path: Path) -> None:
    handbook = tmp_path / "handbook"
    shutil.copytree(HANDBOOK, handbook)
    hours = handbook / "ar" / "hours.md"
    hours.write_text(hours.read_text(encoding="utf-8") + "\nنص إضافي.\n", encoding="utf-8")
    assert check(handbook) == ["ar/hours.md changed since the review"]


def test_a_real_name_and_a_missing_arabic_section_fail_the_checks(tmp_path: Path) -> None:
    handbook = tmp_path / "handbook"
    shutil.copytree(HANDBOOK, handbook)
    insurance = handbook / "en" / "insurance.md"
    insurance.write_text(
        insurance.read_text(encoding="utf-8").replace("Insurer A", "Daman"), encoding="utf-8"
    )
    hours = handbook / "ar" / "hours.md"
    hours.write_text(
        hours.read_text(encoding="utf-8").split("<!-- section: hours.holidays -->")[0],
        encoding="utf-8",
    )
    assert deterministic_checks("insurance.md", handbook)["denylist_hits"] == ["Daman"]
    assert deterministic_checks("hours.md", handbook)["same_sections"] is False
