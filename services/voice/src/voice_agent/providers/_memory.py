"""The process's resident memory, logged after each speech-model load (M2-02)."""

from __future__ import annotations

import resource
import sys


def peak_rss_mb() -> float:
    """Peak resident set size so far, in MB (`ru_maxrss` is bytes on macOS, KiB on Linux)."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024, 1)
