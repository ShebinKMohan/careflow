"""Generate the alert templates for Python and TypeScript from alert_templates.yaml.

The rules engine is pure (no file reads at evaluate time), so the templates are compiled into a
Python module by `make contracts` instead of being read at runtime. Staff screens render the same
lines from `rule_id` when an alert has no display_text (M1-15): ts/alertTemplates.ts.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

CONTRACTS = Path(__file__).resolve().parent.parent
SOURCE = CONTRACTS / "alert_templates.yaml"
TARGET = CONTRACTS / "src" / "careflow_contracts" / "generated" / "alert_templates.py"
TS_TARGET = CONTRACTS / "ts" / "alertTemplates.ts"
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
    TS_TARGET.write_text(typescript(data, rules, qualifiers), encoding="utf-8")
    print(f"gen_alert_templates: wrote {TS_TARGET.relative_to(CONTRACTS.parent.parent)}")
    return 0


def typescript(
    data: dict[str, object], rules: dict[str, dict[str, str]], qualifiers: dict[str, dict[str, str]]
) -> str:
    def entry(values: dict[str, str]) -> str:
        return (
            "{ "
            + ", ".join(f"{lang}: {json.dumps(values[lang], ensure_ascii=False)}" for lang in LANGS)
            + " }"
        )

    header = [
        f"// {line[2:]}" if line.startswith("# ") else "//" for line in HEADER.splitlines()[:1]
    ]
    lines = [
        *header,
        "// source: packages/contracts/alert_templates.yaml",
        "/** Fixed, non-PHI alert lines per rule_id: shown when an alert has no display_text. */",
        f"export const TEMPLATES_VERSION = {int(str(data['version']))};",
        "",
        "export const ALERT_TEMPLATES = {",
        *(f"  {rule_id}: {entry(values)}," for rule_id, values in rules.items()),
        "} as const;",
        "",
        "export const ALERT_QUALIFIERS = {",
        *(f"  {key}: {entry(values)}," for key, values in qualifiers.items()),
        "} as const;",
        "",
        "export type AlertTemplateRule = keyof typeof ALERT_TEMPLATES;",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
