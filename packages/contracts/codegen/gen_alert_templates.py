"""Generate careflow_contracts/generated/alert_templates.py from alert_templates.yaml.

The rules engine is pure (no file reads at evaluate time), so the templates are compiled into a
Python module by `make contracts` instead of being read at runtime.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

CONTRACTS = Path(__file__).resolve().parent.parent
SOURCE = CONTRACTS / "alert_templates.yaml"
TARGET = CONTRACTS / "src" / "careflow_contracts" / "generated" / "alert_templates.py"
HEADER = (CONTRACTS / "codegen" / "header.txt").read_text(encoding="utf-8")
LANGS = ("en", "ar")


def main() -> int:
    data = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    rules: dict[str, dict[str, str]] = data["rules"]
    qualifiers: dict[str, dict[str, str]] = data["qualifiers"]
    lines = [
        HEADER.rstrip("\n"),
        "#",
        "# source: packages/contracts/alert_templates.yaml",
        '"""Fixed, non-PHI alert display templates and required-action keys per rule_id."""',
        "",
        "from typing import Final",
        "",
        f"TEMPLATES_VERSION: Final = {int(data['version'])!r}",
        "",
        "ALERT_TEMPLATES: Final[dict[str, dict[str, str]]] = {",
    ]
    for rule_id, entry in rules.items():
        lines.append(f"    {rule_id!r}: {{")
        lines.extend(f"        {lang!r}: {entry[lang]!r}," for lang in LANGS)
        lines.append("    },")
    lines += ["}", "", "REQUIRED_ACTIONS: Final[dict[str, str]] = {"]
    for rule_id, entry in rules.items():
        lines.append(f"    {rule_id!r}: {entry['required_action']!r},")
    lines += ["}", "", "ALERT_QUALIFIERS: Final[dict[str, dict[str, str]]] = {"]
    for key, entry in qualifiers.items():
        lines.append(f"    {key!r}: {{")
        lines.extend(f"        {lang!r}: {entry[lang]!r}," for lang in LANGS)
        lines.append("    },")
    lines += ["}", ""]
    TARGET.write_text("\n".join(lines), encoding="utf-8")
    print(f"gen_alert_templates: wrote {TARGET.relative_to(CONTRACTS.parent.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
