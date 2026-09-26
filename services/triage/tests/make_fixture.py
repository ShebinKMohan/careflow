"""Write the 60-row seeded synthetic KTAS-format CSV: `python make_fixture.py [path]`.

Synthetic values only; anything trained on it is NOT A RESULT (labelled fixture-*).
"""

from __future__ import annotations

import sys
from pathlib import Path

from careflow_triage.ktas.fixture import write_fixture
from careflow_triage.paths import FIXTURE_CSV

if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else FIXTURE_CSV
    print(write_fixture(target))
