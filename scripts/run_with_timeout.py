#!/usr/bin/env python3
"""Run a command with a time limit: `run_with_timeout.py <seconds> <cmd> [args...]`.

A portable stand-in for coreutils `timeout` (macOS has none; repo scripts prefer `gtimeout` when
it is installed). Exits with the command's status, or 124 on timeout like coreutils.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys

TIMEOUT_EXIT = 124


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: run_with_timeout.py <seconds> <cmd> [args...]", file=sys.stderr)
        return 2
    seconds = float(argv[0])
    proc = subprocess.Popen(argv[1:], start_new_session=True)  # noqa: S603 - caller's command
    try:
        return proc.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        return TIMEOUT_EXIT
    except KeyboardInterrupt:
        os.killpg(proc.pid, signal.SIGTERM)
        return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
