#!/usr/bin/env python3
"""Write each demo persona's synthetic mock national id onto its HAPI Patient (M2-05).

The simulated ID-card tap finds a patient by `Patient?identifier=<mock system>|<id>`. The ids are
in personas.yaml (784-YYYY-NNNNNNN-C, synthetic, never a real Emirates ID). The persona bundles
stay as they are (keypad id only); this adds the second identifier to the loaded Patients, so
running it again changes nothing. Refuses any FHIR base URL that is not localhost.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml

HERE = Path(__file__).resolve().parent
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
FHIR_JSON = {"Content-Type": "application/fhir+json", "Accept": "application/fhir+json"}


def check_local(base_url: str) -> None:
    host = urlparse(base_url).hostname or ""
    if host not in LOCAL_HOSTS:
        raise SystemExit(f"add_mock_ids: refusing non-local FHIR base URL {base_url!r}")


def with_mock_id(patient: dict[str, Any], system: str, value: str) -> dict[str, Any] | None:
    """The Patient with the mock id set, or None when it already has exactly that id."""
    identifiers = [i for i in patient.get("identifier", []) if i.get("system") != system]
    current = [i for i in patient.get("identifier", []) if i.get("system") == system]
    if current == [{"system": system, "value": value}]:
        return None
    return {**patient, "identifier": [*identifiers, {"system": system, "value": value}]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url", default=os.environ.get("FHIR_BASE_URL", "http://localhost:8080/fhir")
    )
    args = parser.parse_args(argv)
    check_local(args.base_url)
    personas = yaml.safe_load((HERE / "personas.yaml").read_text(encoding="utf-8"))
    system = personas["mock_national_id_system"]
    with httpx.Client(base_url=args.base_url.rstrip("/") + "/", headers=FHIR_JSON) as client:
        for persona in personas["personas"]:
            pid, value = persona["fhir_patient_id"], persona["mock_national_id"]
            response = client.get(f"Patient/{pid}")
            if response.status_code == 404:
                print(f"add_mock_ids: Patient/{pid} is not loaded (run make seed first)")
                return 1
            response.raise_for_status()
            updated = with_mock_id(response.json(), system, value)
            masked = f"784-****-*******-{value[-1]}"
            if updated is None:
                print(f"add_mock_ids: Patient/{pid} already has {masked}")
                continue
            client.put(f"Patient/{pid}", json=updated).raise_for_status()
            print(f"add_mock_ids: Patient/{pid} <- {masked}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
