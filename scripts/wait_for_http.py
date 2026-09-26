#!/usr/bin/env python3
"""Poll a URL until it answers 200: `wait_for_http.py <url> <timeout-seconds>` (stdlib only)."""

from __future__ import annotations

import sys
import time
import urllib.error
import urllib.request


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: wait_for_http.py <url> <timeout-seconds>", file=sys.stderr)
        return 2
    url, timeout_s = argv[0], float(argv[1])
    if not url.startswith(("http://", "https://")):
        print("wait_for_http: only http(s) URLs", file=sys.stderr)
        return 2
    start = time.monotonic()
    while True:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 - checked above
                if response.status == 200:
                    print(f"wait_for_http: {url} is up after {time.monotonic() - start:.0f}s")
                    return 0
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        if time.monotonic() - start >= timeout_s:
            print(f"wait_for_http: {url} not up after {timeout_s:.0f}s", file=sys.stderr)
            return 1
        time.sleep(2)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
