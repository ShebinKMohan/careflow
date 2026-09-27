"""Make sure an urgency model is installed for `make up` (M1-19).

If services/triage/artifacts/ holds no trained model: train the hgb baseline on the real KTAS CSV
when it has been downloaded, else on the seeded synthetic fixture CSV. A model trained on the
fixture is labelled `fixture-*` (NOT A RESULT) and the admin page shows that name.
"""

from __future__ import annotations

import subprocess
import sys

from careflow_triage.ktas.fixture import write_fixture
from careflow_triage.paths import ARTIFACTS_DIR, FIXTURE_CSV, KTAS_CSV


def installed() -> list[str]:
    if not ARTIFACTS_DIR.exists():
        return []
    return sorted(p.name for p in ARTIFACTS_DIR.iterdir() if (p / "model.joblib").exists())


def main() -> int:
    found = installed()
    if found:
        print(f"ensure_triage_model: installed: {', '.join(found)}")
        return 0
    csv = KTAS_CSV if KTAS_CSV.exists() else write_fixture(FIXTURE_CSV)
    fixture = "the synthetic fixture (fixture-*, NOT A RESULT)"
    label = "the KTAS data" if csv == KTAS_CSV else fixture
    print(f"ensure_triage_model: no model installed; training hgb on {label}")
    command = ["careflow-triage", "train", "--model", "hgb", "--csv", str(csv)]
    return subprocess.run(command, check=False).returncode  # noqa: S603


if __name__ == "__main__":
    sys.exit(main())
