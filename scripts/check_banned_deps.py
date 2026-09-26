#!/usr/bin/env python3
"""Fail the build when a banned or unconstrained dependency appears.

Checks every pyproject.toml, uv.lock, package.json and pnpm-lock.yaml under the repo root:

* No banned package anywhere. LiteLLM is banned: its PyPI package was compromised in
  March 2026 (docs/decisions/ADR-003-no-litellm.md).
* Every pyproject.toml dependency carries a version constraint (or a direct reference).
  Workspace members and path/workspace sources are exempt: they are pinned by the repo itself.

Exit codes: 0 clean, 1 problem found. Standard library only, so it runs before `uv sync`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

BANNED: frozenset[str] = frozenset({"litellm"})
TARGET_FILES: frozenset[str] = frozenset(
    {"pyproject.toml", "uv.lock", "package.json", "pnpm-lock.yaml"}
)
SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        ".next",
        "dist",
        "build",
        "target",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
    }
)
_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")
_SPECIFIER = re.compile(r"(===|==|~=|!=|<=|>=|<|>)")
# Package keys in pnpm-lock.yaml: `  eslint@10.0.0:`, `  '@scope/pkg@1.0.0':`, `      eslint:`.
_PNPM_KEY = re.compile(r"""^\s+['"]?/?((?:@[\w.-]+/)?[\w.-]+)(?:@[^:'"\s]*)?['"]?:""", re.M)
_NPM_DEP_FIELDS = (
    "dependencies",
    "devDependencies",
    "peerDependencies",
    "optionalDependencies",
)


def normalize(name: str) -> str:
    """PEP 503 normalisation; also fine for npm names (lower-case, no scope change)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def is_banned(name: str) -> bool:
    bare = name.rsplit("/", 1)[-1]  # an npm scope does not launder a banned name
    return normalize(bare) in BANNED


def find_files(root: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for filename in sorted(filenames):
            if filename in TARGET_FILES:
                yield Path(dirpath) / filename


def pyproject_requirements(data: dict[str, Any]) -> Iterator[tuple[str, str]]:
    """Yield (section, requirement) for every dependency declared in a pyproject.toml."""
    project = data.get("project", {})
    for req in project.get("dependencies", []):
        yield "project.dependencies", req
    for extra, reqs in project.get("optional-dependencies", {}).items():
        for req in reqs:
            yield f"project.optional-dependencies.{extra}", req
    for group, reqs in data.get("dependency-groups", {}).items():
        for req in reqs:
            if isinstance(req, str):  # {include-group = "..."} entries are not requirements
                yield f"dependency-groups.{group}", req
    for req in data.get("build-system", {}).get("requires", []):
        yield "build-system.requires", req
    uv = data.get("tool", {}).get("uv", {})
    for key in ("dev-dependencies", "constraint-dependencies", "override-dependencies"):
        for req in uv.get(key, []):
            yield f"tool.uv.{key}", req


def requirement_name(req: str) -> str | None:
    match = _REQ_NAME.match(req)
    return match.group(1) if match else None


def is_constrained(req: str) -> bool:
    spec = req.split(";", 1)[0]  # ignore environment markers
    return bool(_SPECIFIER.search(spec)) or "@" in spec  # version specifier or direct reference


def local_sources(data: dict[str, Any]) -> set[str]:
    sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    return {
        normalize(name)
        for name, source in sources.items()
        if isinstance(source, dict) and ("workspace" in source or "path" in source)
    }


def check_pyproject(path: Path, workspace_names: set[str]) -> list[str]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    exempt = workspace_names | local_sources(data)
    problems: list[str] = []
    for section, req in pyproject_requirements(data):
        name = requirement_name(req)
        if name is None:
            problems.append(f"{section}: cannot parse requirement {req!r}")
            continue
        if is_banned(name):
            problems.append(f"{section}: banned package {name!r} (see ADR-003)")
        elif normalize(name) not in exempt and not is_constrained(req):
            problems.append(f"{section}: {req!r} has no version constraint")
    return problems


def banned_locked(names: set[str]) -> list[str]:
    return [f"locked package {n!r} is banned (see ADR-003)" for n in sorted(names) if is_banned(n)]


def check_uv_lock(path: Path) -> list[str]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return banned_locked({pkg.get("name", "") for pkg in data.get("package", [])})


def check_package_json(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    problems: list[str] = []
    for field in _NPM_DEP_FIELDS:
        for name in data.get(field, {}) or {}:
            if is_banned(name):
                problems.append(f"{field}: banned package {name!r} (see ADR-003)")
    return problems


def check_pnpm_lock(path: Path) -> list[str]:
    return banned_locked(set(_PNPM_KEY.findall(path.read_text(encoding="utf-8"))))


def workspace_package_names(pyprojects: list[Path]) -> set[str]:
    names: set[str] = set()
    for path in pyprojects:
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError):
            continue  # reported by the per-file check
        name = data.get("project", {}).get("name")
        if isinstance(name, str):
            names.add(normalize(name))
    return names


def run(root: Path) -> list[str]:
    files = list(find_files(root))
    pyprojects = [f for f in files if f.name == "pyproject.toml"]
    workspace_names = workspace_package_names(pyprojects)
    problems: list[str] = []
    for path in files:
        rel = path.relative_to(root)
        try:
            if path.name == "pyproject.toml":
                found = check_pyproject(path, workspace_names)
            elif path.name == "uv.lock":
                found = check_uv_lock(path)
            elif path.name == "package.json":
                found = check_package_json(path)
            else:
                found = check_pnpm_lock(path)
        except (tomllib.TOMLDecodeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            found = [f"cannot parse: {exc}"]
        problems.extend(f"{rel}: {msg}" for msg in found)
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail on banned or unconstrained dependencies.")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="repository root to scan (default: this repo)",
    )
    args = parser.parse_args(argv)
    problems = run(args.root.resolve())
    if problems:
        print(f"check_banned_deps: {len(problems)} problem(s)", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print("check_banned_deps: OK (no banned or unconstrained dependencies)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
