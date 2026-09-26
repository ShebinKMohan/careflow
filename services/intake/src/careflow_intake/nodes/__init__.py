"""Graph nodes. Each takes the state, the ports and the settings, and returns a state update.

A node that calls `interrupt()` re-runs from its top when the graph resumes, so those nodes
(`await_answer`, `nurse_review`) do nothing with side effects before their interrupt.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from careflow_intake.ports import IntakePorts
from careflow_intake.state import IntakeSettings, IntakeState

type Update = dict[str, Any]
type NodeFn = Callable[[IntakeState, IntakePorts, IntakeSettings], Awaitable[Update]]

__all__ = ["NodeFn", "Update"]
