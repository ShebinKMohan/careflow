"""Tests for scripts/run_with_timeout.py, the portable stand-in for coreutils `timeout`."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "run_with_timeout.py"


def run(*args: str) -> int:
    return subprocess.run([sys.executable, str(SCRIPT), *args], check=False).returncode


def test_passes_through_the_exit_status() -> None:
    assert run("5", sys.executable, "-c", "raise SystemExit(3)") == 3


def test_returns_124_on_timeout() -> None:
    assert run("0.5", sys.executable, "-c", "import time; time.sleep(30)") == 124


def test_usage_error() -> None:
    assert run("5") == 2
