"""The doctor briefings the document tests use (M1-16), as the intake builds them.

    uv run python services/integration/tests/fixtures/briefing/generate.py

Built by `careflow_intake.briefing.make_briefing` from the intake's persona fixtures (chest pain:
EHR record, device and nurse vitals, an alert and a completed ECG; child: no EHR conditions and no
model suggestion), without a summary. Synthetic data only.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path

from careflow_intake.briefing import make_briefing
from careflow_intake.briefing.builder import briefing_json
from careflow_intake.briefing.record import load_persona

HERE = Path(__file__).resolve().parent


async def main() -> None:
    for persona in ("chest-pain", "child-drowsy"):
        state = load_persona(persona)
        decision = state.get("nurse_decision")
        assert decision is not None
        result = await make_briefing(state, now=decision.decided_at + timedelta(minutes=1))
        assert result.problems == [], result.problems
        body = {"patient_ref": state.get("patient_ref"), "briefing": briefing_json(result.briefing)}
        path = HERE / f"{persona}.json"
        path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{persona} -> {path.name}")


if __name__ == "__main__":
    asyncio.run(main())
