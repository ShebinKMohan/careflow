"""FHIR model types stay inside careflow_integration (M1-08)."""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PACKAGES = {
    "careflow_api": REPO / "services" / "api" / "src" / "careflow_api",
    "careflow_intake": REPO / "services" / "intake" / "src" / "careflow_intake",
    "careflow_rules": REPO / "services" / "rules" / "src" / "careflow_rules",
}


def fhir_imports(root: Path) -> list[str]:
    found = []
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            where = f"{root.name}/{path.relative_to(root)}"
            found += [f"{where}: {n}" for n in names if n == "fhir" or n.startswith("fhir.")]
    return found


def test_no_other_package_imports_fhir_resources() -> None:
    scanned = {name: len(list(root.rglob("*.py"))) for name, root in PACKAGES.items()}
    assert all(count > 3 for count in scanned.values()), scanned  # the scan really looks
    assert [line for root in PACKAGES.values() for line in fhir_imports(root)] == []


def test_the_scan_finds_a_fhir_import(tmp_path: Path) -> None:
    (tmp_path / "leak.py").write_text(
        "from fhir.resources.R4B.patient import Patient\n", encoding="utf-8"
    )
    assert [line.rsplit(": ", 1)[1] for line in fhir_imports(tmp_path)] == [
        "fhir.resources.R4B.patient"
    ]
