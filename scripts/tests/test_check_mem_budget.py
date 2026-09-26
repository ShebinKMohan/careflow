"""Tests for scripts/check_mem_budget.py."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "check_mem_budget.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_mem_budget", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(1073741824, 1024**3), ("1g", 1024**3), ("256m", 256 * 1024**2), ("2560MB", 2560 * 1024**2)],
)
def test_parse_bytes(raw: int | str, expected: int) -> None:
    assert load().parse_bytes(raw) == expected


def test_parse_bytes_rejects_nonsense() -> None:
    with pytest.raises(ValueError, match="unrecognised"):
        load().parse_bytes("lots")


def test_profiled_services_are_ignored_and_missing_limits_reported() -> None:
    config = {
        "services": {
            "a": {"mem_limit": "1g"},
            "b": {"mem_limit": 268435456},
            "c": {"profiles": ["obs"], "mem_limit": "8g"},
            "d": {},
        }
    }
    assert load().default_service_limits(config) == {"a": 1024**3, "b": 256 * 1024**2, "d": None}


@pytest.mark.skipif(shutil.which("docker") is None, reason="requires the docker CLI")
def test_the_real_compose_file_fits_the_budget() -> None:
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "of 6.00 GiB" in result.stdout
