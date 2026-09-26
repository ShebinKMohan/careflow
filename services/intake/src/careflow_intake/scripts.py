"""Scripted synthetic patients through the real intake (M1-07): the real extractor, rules and
checklist conversation in the M1-05 graph, answering each question from the script.

    scripts/with_llm.sh uv run python -m careflow_intake.scripts --record chest_pain

Recording runs the local model for extraction inside the M0-06 `ReplayProvider` (record mode), so
`<script>.cassette.json` holds every extraction reply; tests replay it without a model. Questions
use the approved templates (`llm_phrasing: false`), so a replay is deterministic.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Final

import yaml

from careflow_contracts.generated.structured_facts_schema import Language, TurnRole
from careflow_intake.checkpoint import memory_checkpointer
from careflow_intake.extraction.extractor import TASK
from careflow_intake.extraction.record import warm_up
from careflow_intake.facts import FactEntry, stable_fact_id
from careflow_intake.graph import IntakeGraph, add_facts, answer, build_graph, pending, start_intake
from careflow_intake.ports import IntakePorts
from careflow_intake.stubs import START
from careflow_intake.wiring import RealPortsSettings, build_real_ports, providers_config
from careflow_providers.adapters.replay import ReplayProvider
from careflow_providers.base import ChatProvider
from careflow_providers.factory import build_provider

SCRIPTS: Final = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "scripts"
ACKNOWLEDGE: Final = "OK"
MAX_STEPS: Final = 20


@dataclass(frozen=True)
class Script:
    id: str
    path: Path
    body: dict[str, Any]

    @property
    def answers(self) -> dict[str, str]:
        return dict(self.body["answers"])

    @property
    def expect(self) -> dict[str, Any]:
        return dict(self.body["expect"])

    @property
    def cassette(self) -> Path:
        return self.path.with_suffix(".cassette.json")

    def device_facts(self) -> list[FactEntry]:
        """The script's device readings as DEVICE_MEASURED facts."""
        entries = []
        for index, reading in enumerate(self.body.get("device_facts", []), start=1):
            fact = {
                "fact_id": stable_fact_id(self.id, "device", str(index)),
                "provenance": "DEVICE_MEASURED",
                "source": {
                    "kind": "device",
                    "device_id": "dev_kiosk_1",
                    "reading_id": f"rd_{index:04d}",
                },
                "recorded_at": START + timedelta(seconds=10),
                "value": reading["value"],
                "unit": reading["unit"],
            }
            entries.append(FactEntry.model_validate({"key": reading["key"], "fact": fact}))
        return entries


def load_script(name: str, scripts: Path = SCRIPTS) -> Script:
    path = scripts / f"{name}.yaml"
    body = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Script(id=str(body["id"]), path=path, body=body)


@dataclass
class ScriptRun:
    result: dict[str, Any]
    asked: list[str] = field(default_factory=list)  # question keys in order ("" for a notice)
    questions: list[str] = field(default_factory=list)  # the texts shown to the patient

    @property
    def patient_turns(self) -> int:
        return sum(1 for turn in self.result["turns"] if turn.role is TurnRole.patient)


def script_ports(script: Script, extraction: ChatProvider, **stubs: Any) -> IntakePorts:
    """Real extractor (over `extraction`), rules and conversation with approved templates only."""
    settings = RealPortsSettings(llm_phrasing=False, extraction_provider=extraction)
    return build_real_ports(settings, **stubs)


def replay(script: Script) -> ReplayProvider:
    entries = json.loads(script.cassette.read_text(encoding="utf-8"))
    model = next(iter(entries.values()))["model"]
    return ReplayProvider(None, task=TASK, model=model, cassette=script.cassette, mode="replay")


async def run_script(script: Script, ports: IntakePorts) -> ScriptRun:
    """Start the intake, add the device readings, and answer every question from the script until
    the graph pauses for the nurse."""
    graph: IntakeGraph = build_graph(ports, memory_checkpointer())
    session = script.body["session"]
    sid = session["session_id"]
    result = await start_intake(
        graph,
        session_id=sid,
        clinic_id=session["clinic_id"],
        arrived_at=START,
        language=Language(session.get("language", "en")),
    )
    if devices := script.device_facts():
        await add_facts(graph, sid, devices)
    run = ScriptRun(result=result)
    for _ in range(MAX_STEPS):
        waiting = pending(result)
        if waiting is None or waiting["kind"] != "patient_input":
            break
        question = waiting["question"]
        key = question.get("key")
        run.asked.append(key or "")
        run.questions.append(question["text"])
        default = str(script.body.get("default_answer", ACKNOWLEDGE))
        text = script.answers.get(key, default) if key else ACKNOWLEDGE  # a notice is acknowledged
        result = await answer(graph, sid, text)
    run.result = result
    return run


async def record(name: str) -> ScriptRun:
    """Run the script with the live model and save its extraction replies to the cassette."""
    script = load_script(name)
    providers = providers_config()
    inner = build_provider(TASK, providers)
    await warm_up(inner)
    if script.cassette.exists():
        script.cassette.unlink()  # a fresh recording, not a merge with an older prompt's replies
    provider = ReplayProvider(
        inner, task=TASK, model=inner.model_name, cassette=script.cassette, mode="record"
    )
    return await run_script(script, script_ports(script, provider))


def summary(run: ScriptRun) -> str:
    flags = run.result.get("flags", {})
    alerts = [alert.rule_id.value for alert in run.result.get("alerts", [])]
    facts = {e.key.value: e.base.model_dump(mode="json")["value"] for e in run.result["facts"]}
    return (
        f"asked {run.asked}\nalerts {alerts}\nflags {flags}\npatient turns {run.patient_turns}\n"
        f"facts {facts}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record a scripted patient's extraction replies.")
    parser.add_argument("--record", required=True, help="script name, e.g. chest_pain")
    args = parser.parse_args(argv)
    run = asyncio.run(record(args.record))
    print(summary(run))
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["SCRIPTS", "Script", "ScriptRun", "load_script", "replay", "run_script", "script_ports"]
