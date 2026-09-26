#!/usr/bin/env python3
"""Load the Synthea population and the seven demo personas into the local HAPI FHIR server.

- Refuses any FHIR base URL that is not localhost: synthetic data only, never a real server.
- Synthea: hospital and practitioner bundles first (patient bundles use conditional references to
  them), then every patient bundle, each POSTed as a transaction (120 s timeout). Skipped when the
  server already holds >= 200 patients, unless FORCE=1.
- Personas: transaction bundles that PUT fixed ids, so reloading changes nothing. `__TODAY__` in a
  bundle becomes today's date (appointments booked today).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

HERE = Path(__file__).resolve().parent
SYNTHEA_DIR = HERE / "output" / "fhir"
PERSONA_DIR = HERE / "personas"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
SKIP_AT = 200
TIMEOUT = httpx.Timeout(120.0, connect=10.0)
FHIR_JSON = {"Content-Type": "application/fhir+json", "Accept": "application/fhir+json"}


def check_local(base_url: str) -> None:
    host = urlparse(base_url).hostname or ""
    if host not in LOCAL_HOSTS:
        raise SystemExit(f"load_fhir: refusing non-local FHIR base URL {base_url!r}")


def patient_count(client: httpx.Client) -> int:
    # no-cache: HAPI otherwise reuses a cached search result for about a minute.
    response = client.get(
        "Patient", params={"_summary": "count"}, headers={"Cache-Control": "no-cache"}
    )
    response.raise_for_status()
    return int(response.json().get("total", 0))


def post_bundle(
    client: httpx.Client, bundle: dict[str, Any], label: str, attempts: int = 3
) -> None:
    for attempt in range(1, attempts + 1):
        response = client.post("", json=bundle, headers=FHIR_JSON)
        if response.status_code < 300:
            return
        if attempt == attempts or response.status_code < 500 and response.status_code != 409:
            raise RuntimeError(f"{label}: HTTP {response.status_code}: {response.text[:300]}")
        time.sleep(2 * attempt)  # transient conflicts while parallel bundles share references


def load_synthea(client: httpx.Client, workers: int) -> int:
    files = sorted(SYNTHEA_DIR.glob("*.json"))
    if not files:
        raise SystemExit(f"load_fhir: no bundles in {SYNTHEA_DIR}; run infra/seed/run_synthea.sh")
    shared_prefixes = ("hospitalInformation", "practitionerInformation")
    shared = [f for f in files if f.name.startswith(shared_prefixes)]
    patients = [f for f in files if f not in shared]
    for path in shared:  # sequential and first: patient bundles reference these conditionally
        post_bundle(client, json.loads(path.read_text(encoding="utf-8")), path.name)
    print(f"load_fhir: loaded {len(shared)} hospital/practitioner bundles")
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(post_bundle, client, json.loads(p.read_text(encoding="utf-8")), p.name): p
            for p in patients
        }
        for future in as_completed(futures):
            future.result()
            done += 1
            if done % 25 == 0 or done == len(patients):
                print(f"load_fhir: {done}/{len(patients)} patient bundles")
    return len(patients)


def load_personas(client: httpx.Client, today: str) -> int:
    paths = sorted(PERSONA_DIR.glob("*.json"))
    for path in paths:
        bundle = json.loads(path.read_text(encoding="utf-8").replace("__TODAY__", today))
        post_bundle(client, bundle, path.name)
    return len(paths)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    default_base = os.environ.get("FHIR_BASE_URL", "http://localhost:8080/fhir")
    parser.add_argument("--base-url", default=default_base)
    parser.add_argument("--personas-only", action="store_true", help="skip Synthea patients")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    base_url = args.base_url.rstrip("/") + "/"
    check_local(base_url)
    force = os.environ.get("FORCE", "0") == "1"

    with httpx.Client(base_url=base_url, timeout=TIMEOUT) as client:
        before = patient_count(client)
        print(f"load_fhir: {before} patients on {base_url}")
        if args.personas_only:
            print("load_fhir: personas only (SEED_SYNTHEA=0)")
        elif before >= SKIP_AT and not force:
            print(f"load_fhir: already >= {SKIP_AT} patients; skipping Synthea (FORCE=1 reloads)")
        else:
            load_synthea(client, args.workers)
        loaded = load_personas(client, dt.date.today().isoformat())
        after = patient_count(client)
    print(f"load_fhir: {loaded} persona bundles upserted; {after} patients now (+{after - before})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
