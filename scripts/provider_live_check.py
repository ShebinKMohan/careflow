#!/usr/bin/env python3
"""Live smoke test of the provider layer: structured() -> StructuredFacts from one patient turn.

Run through the local model: `scripts/with_llm.sh uv run python scripts/provider_live_check.py`.
This checks the pipeline (provider, JSON Schema prompt, validation, repair retry), not extraction
quality: M1-06 builds the real extractor with verified source spans. Synthetic text only.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from careflow_contracts import StructuredFacts
from careflow_contracts.validators import check_structured_facts
from careflow_providers import StructuredOk, build_provider, load_config, structured

REPO_ROOT = Path(__file__).resolve().parent.parent
PATIENT_TEXT = "I have crushing chest pain since 20 minutes"
FACT_IDS = [f"f_01K62Q00000000000000000{i:03d}" for i in range(1, 10)]
SYSTEM = f"""You extract structured intake facts for the CareFlow triage kiosk (research prototype,
synthetic data). Include only facts the patient stated: omit every other field. Here that means
the top-level keys session_id, clinic_id, language, extraction_status, chief_complaint and
chest_pain (present, onset) only; no patient, vitals, ehr_context or other groups.
- session_id "ses_live_check", clinic_id "clinic_demo", language "en", extraction_status "complete".
- Use these fact ids in order: {", ".join(FACT_IDS)}.
- Every fact: provenance "PATIENT_STATED", recorded_at "2026-09-26T09:00:00Z", and a source
  {{"kind": "transcript_span", "turn_id": "turn_01", "start": <int>, "end": <int>, "quote": <text>}}
  where quote is exactly the patient's text between the character offsets start and end.
- Tri-state values are "yes", "no" or "unknown". Durations use ISO 8601 (e.g. PT20M)."""


async def main() -> int:
    config = load_config(REPO_ROOT / "config" / "providers.yaml")
    provider = build_provider("extraction", config)
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f'Patient turn turn_01: "{PATIENT_TEXT}"'},
    ]
    start = time.perf_counter()
    result = await structured(provider, messages, StructuredFacts)  # type: ignore[arg-type]
    elapsed = time.perf_counter() - start
    if not isinstance(result, StructuredOk):
        print(f"StructuredFailure after {elapsed:.1f}s: {result.errors[-1][:500]}")
        return 1
    facts = result.value
    print(
        f"StructuredOk: attempts={result.attempts} elapsed={elapsed:.1f}s "
        f"model={provider.model_name}"
    )
    print(json.dumps(facts.model_dump(mode="json", exclude_none=True), indent=2))
    problems = check_structured_facts(facts)
    print(f"cross-checks (provenance, spans, unique ids): {problems or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
