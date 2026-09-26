"""careflow_rules is pure: no I/O, network, database, model provider, clock or randomness."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

import careflow_rules

PACKAGE: Final = Path(careflow_rules.__file__).parent
FORBIDDEN_MODULES: Final = {
    "httpx", "requests", "sqlalchemy", "redis", "openai", "anthropic", "langchain", "langgraph",
    "socket", "urllib", "http", "subprocess", "asyncio", "random", "secrets", "time", "os",
    "pathlib", "shutil", "tempfile", "numpy", "pandas", "careflow_providers",
}  # fmt: skip
FORBIDDEN_CALLS: Final = {"open", "eval", "exec", "compile", "__import__", "input"}
CLOCK_METHODS: Final = {"now", "utcnow", "today", "fromtimestamp"}  # rules use the `now` passed in


def violations(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        found += [f"import {m}" for m in modules if m.split(".")[0] in FORBIDDEN_MODULES]
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
                found.append(f"call {node.func.id}()")
            if isinstance(node.func, ast.Attribute) and node.func.attr in CLOCK_METHODS:
                found.append(f"clock .{node.func.attr}()")
    return sorted(found)


def test_rules_package_is_pure() -> None:
    sources = sorted(PACKAGE.rglob("*.py"))
    assert len(sources) >= 6
    report = {
        str(path.relative_to(PACKAGE)): found
        for path in sources
        if (found := violations(path.read_text(encoding="utf-8")))
    }
    assert report == {}


def test_the_scan_catches_each_kind_of_violation() -> None:
    probe = (
        "import socket\nfrom httpx import get\nimport datetime\n"
        "x = open('f')\ny = datetime.datetime.now()\nz = ctx.now\n"
    )
    assert violations(probe) == ["call open()", "clock .now()", "import httpx", "import socket"]
