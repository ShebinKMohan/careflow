"""Wait until every CareFlow process is ready, then print the URLs and "CareFlow is up".

    python3 scripts/wait_healthy.py api=http://127.0.0.1:8000/health staff=http://localhost:3001 ...

A service is ready when its URL answers 2xx or 3xx; the API (`api=`) also needs its dependencies:
its `/health` must report the database, Valkey and the FHIR server `ok` (an HTTP 200 alone only
says the process is alive). Gives up after --timeout seconds (default 240) and names what is not
ready, and why. The printed URLs follow the stack's ports (scripts/stack.py). Standard library.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

#: The API's /health components that must be "ok" before the stack is up.
API_COMPONENTS = ("db", "redis", "fhir")


def check(name: str, url: str) -> str | None:
    """None when ready, else why not."""
    try:
        with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310 (local URLs only)
            if not 200 <= response.status < 400:
                return f"HTTP {response.status}"
            if name != "api":
                return None
            body = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return None if 300 <= exc.code < 400 and name != "api" else f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        return f"not answering ({type(exc).__name__})"
    except json.JSONDecodeError:
        return "health is not JSON"
    down = [key for key in API_COMPONENTS if body.get(key) != "ok"]
    return f"dependencies not ready: {', '.join(down)}" if down else None


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("services", nargs="+", help="name=url")
    parser.add_argument("--timeout", type=float, default=240.0)
    args = parser.parse_args()
    services = dict(item.split("=", 1) for item in args.services)
    pending = dict(services)
    reasons: dict[str, str] = {}
    deadline = time.monotonic() + args.timeout
    while pending and time.monotonic() < deadline:
        for name, url in list(pending.items()):
            reason = check(name, url)
            if reason is None:
                print(f"  {name:<10} up    {url}")
                del pending[name]
            else:
                reasons[name] = reason
        if pending:
            time.sleep(2)
    if pending:
        for name, url in pending.items():
            print(f"  {name:<10} DOWN  {url}: {reasons.get(name, 'no answer')}", file=sys.stderr)
        print("CareFlow is NOT up: see `make logs`.", file=sys.stderr)
        return 1
    port = os.environ.get
    print("CareFlow is up.")
    print(f"  Kiosk      http://localhost:{port('CAREFLOW_KIOSK_PORT', '3000')}")
    print(f"  Staff      http://localhost:{port('CAREFLOW_STAFF_PORT', '3001')}")
    print("             /nurse, /doctor, /board?department=ed, /admin/status")
    print(f"  Companion  http://localhost:{port('CAREFLOW_COMPANION_PORT', '3002')}")
    print(f"  API        http://localhost:{port('CAREFLOW_API_PORT', '8000')}/health")
    print("Research prototype. Synthetic data only. Not for clinical use.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
