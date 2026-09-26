"""A seeded synthetic table in the KTAS column format, for CI and for when no data is available.

Values are random and only loosely tied to the level: training on it proves the pipeline, not a
model. Anything trained on it is labelled `fixture-*` and its reports say NOT A RESULT.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from careflow_triage.ktas.load import COLUMNS

COMPLAINTS = (
    "abd pain",
    "dyspnea",
    "dizziness",
    "fever",
    "ant. chest pain",
    "open wound",
    "headache",
    "mental change",
)


def write_fixture(path: Path, *, rows: int = 60, seed: int = 42) -> Path:
    """Write `rows` synthetic rows (12 per level for 60) as a ';'-separated CSV with decimal commas
    and the real file's placeholders, so the loader's quirk handling runs too."""
    rng = np.random.default_rng(seed)
    lines = [";".join(COLUMNS)]
    for i in range(rows):
        level = i % 5 + 1
        severity = (5 - level) / 4  # 1 for level 1, 0 for level 5
        group = 1 + int(rng.random() < 0.5)
        hr = round(80 + 50 * severity + rng.normal(0, 8))
        spo2 = "" if group == 1 else str(min(100, round(98 - 8 * severity + rng.normal(0, 1))))
        values = {
            "Group": str(group),
            "Sex": str(1 + int(rng.random() < 0.5)),
            "Age": f"{rng.uniform(16, 90):.1f}".replace(".", ","),
            "Patients number per hour": str(int(rng.integers(1, 12))),
            "Arrival mode": str(2 if severity > 0.6 else int(rng.choice([1, 3, 4, 6]))),
            "Injury": str(1 + int(rng.random() < 0.2)),
            "Chief_complain": COMPLAINTS[int(rng.integers(0, len(COMPLAINTS)))],
            "Mental": str(1 if severity < 0.9 or rng.random() < 0.5 else int(rng.integers(2, 5))),
            "Pain": str(int(rng.random() < 0.55)),
            "NRS_pain": "#NULL!" if rng.random() < 0.4 else str(int(rng.integers(1, 9))),
            "SBP": "측불"
            if rng.random() < 0.03
            else str(round(125 - 30 * severity + rng.normal(0, 10))),
            "DBP": str(round(78 - 12 * severity + rng.normal(0, 6))),
            "HR": str(hr),
            "RR": str(round(18 + 8 * severity + rng.normal(0, 2))),
            "BT": f"{36.6 + 1.6 * severity + rng.normal(0, 0.3):.1f}".replace(".", ","),
            "Saturation": spo2,
            "KTAS_RN": str(min(5, max(1, level + int(rng.choice([-1, 0, 0, 0, 1]))))),
            "Diagnosis in ED": "synthetic",
            "Disposition": str(int(rng.integers(1, 3))),
            "KTAS_expert": str(level),
            "Error_group": "0",
            "Length of stay_min": str(int(rng.integers(30, 600))),
            "KTAS duration_min": f"{rng.uniform(1, 10):.2f}".replace(".", ","),
            "mistriage": "0",
        }
        lines.append(";".join(values[c] for c in COLUMNS))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
