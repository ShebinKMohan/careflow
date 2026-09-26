"""Display labels for a KTAS level in other scales (a display mapping only)."""

from __future__ import annotations

from functools import cache
from typing import Any

import yaml

from careflow_triage.paths import CONFIG_DIR


@cache
def scales() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(
        (CONFIG_DIR / "triage_scales.yaml").read_text(encoding="utf-8")
    )
    return data


def display_label(level: int, scale: str = "KTAS") -> str:
    return str(scales()["scales"][scale][level])
