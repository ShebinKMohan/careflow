"""Vercel entrypoint for the original CareFlow API, with explicit hosted adapters."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for base in ("packages", "services"):
    for source in (ROOT / base).glob("*/src"):
        sys.path.insert(0, str(source))

from careflow_api.main import create_app  # noqa: E402

app = create_app()
