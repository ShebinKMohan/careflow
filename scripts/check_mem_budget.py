#!/usr/bin/env python3
"""Fail when the default-profile Docker services exceed the memory budget (6 GiB).

Reads `docker compose -f infra/docker-compose.yml config --no-interpolate --format json`, sums
`mem_limit` over the services without a profile, and exits 1 if the total is over budget or a
default service declares no limit. Docker plus one local LLM must fit in 18 GB of RAM.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

BUDGET_BYTES = 6 * 1024**3
REPO_ROOT = Path(__file__).resolve().parent.parent
_UNITS = {"": 1, "b": 1, "k": 1024, "kb": 1024, "m": 1024**2, "mb": 1024**2, "g": 1024**3}
_UNITS |= {"gb": 1024**3}
_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]*)\s*$")


def parse_bytes(value: int | float | str) -> int:
    """`1073741824`, `"1g"`, `"256m"`, `"2560MB"` -> bytes."""
    if isinstance(value, int | float):
        return int(value)
    match = _SIZE.match(value)
    if not match or match.group(2).lower() not in _UNITS:
        raise ValueError(f"unrecognised memory size {value!r}")
    return int(float(match.group(1)) * _UNITS[match.group(2).lower()])


def default_service_limits(config: dict[str, Any]) -> dict[str, int | None]:
    """mem_limit in bytes (None when missing) for every service without a profile."""
    limits: dict[str, int | None] = {}
    for name, service in config.get("services", {}).items():
        if service.get("profiles"):
            continue
        raw = service.get("mem_limit")
        limits[name] = None if raw is None else parse_bytes(raw)
    return limits


def load_config(compose_file: Path) -> dict[str, Any]:
    result = subprocess.run(  # noqa: S603 - fixed argv
        ["docker", "compose", "-f", str(compose_file), "config", "--no-interpolate"]  # noqa: S607
        + ["--format", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the Docker memory budget.")
    parser.add_argument("--compose-file", type=Path, default=REPO_ROOT / "infra/docker-compose.yml")
    parser.add_argument("--budget-gib", type=float, default=BUDGET_BYTES / 1024**3)
    args = parser.parse_args(argv)
    limits = default_service_limits(load_config(args.compose_file))
    missing = [name for name, limit in limits.items() if limit is None]
    total = sum(limit for limit in limits.values() if limit is not None)
    budget = int(args.budget_gib * 1024**3)
    for name, limit in sorted(limits.items()):
        shown = "MISSING" if limit is None else f"{limit / 1024**2:.0f} MiB"
        print(f"  {name:<12} {shown}")
    print(f"check_mem_budget: total {total / 1024**3:.2f} GiB of {budget / 1024**3:.2f} GiB")
    if missing:
        names = ", ".join(missing)
        print(f"check_mem_budget: services without mem_limit: {names}", file=sys.stderr)
        return 1
    if total > budget:
        print("check_mem_budget: over budget", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
