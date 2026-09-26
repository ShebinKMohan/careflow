"""Record the HAPI FHIR answers for a seeded persona into tests/fixtures/fhir/<persona>/ (M1-08).

    uv run python -m careflow_integration.fhir.record --persona chest-pain   (needs `make up`)

Each file holds the resources one search returned, so the offline tests can replay them. The
personas are synthetic (infra/seed/personas.yaml).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import yaml

from careflow_integration.adapters.loader import REPO, load_adapter
from careflow_integration.fhir.client import FhirClient

PERSONAS: Final = REPO / "infra" / "seed" / "personas.yaml"
FIXTURES: Final = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "fhir"


def personas() -> dict[str, dict[str, Any]]:
    body = yaml.safe_load(PERSONAS.read_text(encoding="utf-8"))
    return {persona["id"]: persona for persona in body["personas"]}


async def record(persona_id: str, *, today: str | None = None) -> Path:
    persona = personas()[persona_id]
    adapter = load_adapter()
    out = FIXTURES / persona_id
    out.mkdir(parents=True, exist_ok=True)
    day = today or datetime.now(UTC).date().isoformat()
    async with FhirClient(adapter.fhir_base_url) as client:
        system = adapter.identifier_systems["keypad"]
        patients = await client.search(
            "Patient", {"identifier": f"{system}|{persona['keypad_id']}"}
        )
        answers: dict[str, list[Any]] = {"Patient": patients}
        pid = patients[0]["id"]
        answers["AllergyIntolerance"] = await client.search(
            "AllergyIntolerance", {"patient": pid, "clinical-status": "active"}
        )
        answers["Condition"] = await client.search(
            "Condition", {"patient": pid, "clinical-status": "active"}
        )
        for kind in adapter.medication_types:
            answers[kind] = await client.search(kind, {"patient": pid, "status": "active"})
        answers["Appointment"] = await client.search(
            "Appointment", {"patient": pid, "date": f"ge{day}"}
        )
    for kind, resources in answers.items():
        text = json.dumps(resources, indent=1, sort_keys=True, ensure_ascii=False)
        (out / f"{kind}.json").write_text(text + "\n", encoding="utf-8")
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record HAPI answers for a synthetic persona.")
    parser.add_argument("--persona", required=True, choices=sorted(personas()))
    parser.add_argument("--today", help="the date for the Appointment search (default: today, UTC)")
    args = parser.parse_args(argv)
    out = asyncio.run(record(args.persona, today=args.today))
    print(f"recorded {args.persona} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
