"""Wait until every CareFlow process answers, then print the URLs and "CareFlow is up".

    python3 scripts/wait_healthy.py api=http://127.0.0.1:8000/health staff=http://localhost:3001 ...

A service is green when its URL answers 2xx or 3xx. Gives up after --timeout seconds (default
240) and names the ones still down. Standard library only.
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request


def green(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310 (local URLs only)
            return 200 <= response.status < 400
    except urllib.error.HTTPError as exc:
        return 300 <= exc.code < 400
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("services", nargs="+", help="name=url")
    parser.add_argument("--timeout", type=float, default=240.0)
    args = parser.parse_args()
    services = dict(item.split("=", 1) for item in args.services)
    pending = dict(services)
    deadline = time.monotonic() + args.timeout
    while pending and time.monotonic() < deadline:
        for name, url in list(pending.items()):
            if green(url):
                print(f"  {name:<10} up    {url}")
                del pending[name]
        if pending:
            time.sleep(2)
    if pending:
        for name, url in pending.items():
            print(f"  {name:<10} DOWN  {url}", file=sys.stderr)
        print("CareFlow is NOT up: see `make logs`.", file=sys.stderr)
        return 1
    print("CareFlow is up.")
    print("  Kiosk      http://localhost:3000")
    print("  Staff      http://localhost:3001")
    print("             /nurse, /doctor, /board?department=ed, /admin/status")
    print("  Companion  http://localhost:3002")
    print("  API        http://localhost:8000/health")
    print("Research prototype. Synthetic data only. Not for clinical use.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
