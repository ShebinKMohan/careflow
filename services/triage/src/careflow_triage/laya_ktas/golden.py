"""`careflow-triage laya fixtures`: 50 synthetic states and their calibrated probabilities (M3-01).

The golden file for M3-02's ONNX parity checks. The states are synthetic (seeded, plausible
values, no KTAS row), the model runs on the CPU in fp32 so a re-run gives the same numbers, and
the probabilities are rounded to 6 decimals: re-running rewrites `golden.json` byte-identically.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import numpy as np

from careflow_triage.laya_ktas.serialize import (
    KTAS_QUESTION,
    NURSE_ONLY_KEYS,
    STATE_KEYS,
    STATE_VERSION,
)

GOLDEN: Final = Path(__file__).with_name("fixtures") / "golden.json"
COUNT: Final = 50
SEED: Final = 42
COMPLAINTS: Final = (
    "chest pain",
    "shortness of breath",
    "abdominal pain",
    "headache",
    "dizziness",
    "fever",
    "cut on the hand",
    "ankle injury after a fall",
    "vomiting since last night",
    "back pain",
    "sore throat",
    "palpitations",
    "weakness on one side",
)
ACVPU: Final = ("alert", "alert", "alert", "new confusion", "responds to voice")


def synthetic_states(count: int = COUNT, seed: int = SEED) -> list[dict[str, str]]:
    rng = np.random.default_rng(seed)

    def pick(options: tuple[str, ...]) -> str:
        return options[int(rng.integers(len(options)))]

    states = []
    for i in range(count):
        values = {
            "age": f"{rng.integers(16, 93)} years",
            "sex": pick(("male", "female")),
            "chief_complaint": pick(COMPLAINTS),
            "pain_score": f"{rng.integers(0, 11)}/10",
            "heart_rate": f"{rng.integers(48, 151)} /min",
            "spo2": f"{rng.integers(84, 101)} %",
            "temperature": f"{rng.uniform(35.5, 40.2):.1f} °C",
            "respiratory_rate": f"{rng.integers(10, 35)} /min",
            "systolic_bp": f"{rng.integers(80, 191)} mmHg",
            "consciousness": pick(ACVPU),
        }
        keep = set(STATE_KEYS) - (set(NURSE_ONLY_KEYS) if i % 3 == 0 else set())
        if i % 7 == 0:
            keep.discard("pain_score")
        states.append({key: values[key] for key in STATE_KEYS if key in keep})
    return states


def write_golden(run_dir: Path, path: Path = GOLDEN) -> Path:
    import laya  # type: ignore[import-not-found]

    agent = laya.Agent(str(run_dir), device="cpu")
    states = synthetic_states()
    results = agent.predict_batch(states, KTAS_QUESTION, batch_size=10)
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = []
    for state, result in zip(states, results, strict=True):
        answer = result["answers"]["urgency"]
        cases.append(
            {
                "state": state,
                "choice": str(answer["choice"]),
                "probabilities": {
                    str(k): round(float(v), 6) for k, v in answer["probabilities"].items()
                },
            }
        )
    body = {
        "state_version": STATE_VERSION,
        "model_id": "laya-ktas",
        "model_version": meta["run_id"],
        "temperature": agent.temperature[0],
        "device": "cpu",
        "synthetic": True,
        "cases": cases,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


__all__ = ["COUNT", "GOLDEN", "synthetic_states", "write_golden"]
