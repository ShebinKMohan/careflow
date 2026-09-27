"""run_rules and the route after it. The only way to `suggest_urgency` is through here
(safety rule 1)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from careflow_contracts.generated.events.alert_raised_schema import AlertRaisedData
from careflow_contracts.generated.events.envelope_schema import EventType
from careflow_intake.nodes import Update
from careflow_intake.ports import IntakePorts
from careflow_intake.state import IntakeSettings, IntakeState, structured_facts


def caps_reached(state: IntakeState, now: datetime, settings: IntakeSettings) -> bool:
    """The conversation stops after `max_turns` patient answers or `max_seconds`."""
    if state.get("turn_count", 0) >= settings.max_turns:
        return True
    started_at = state.get("started_at")
    return started_at is not None and (now - started_at).total_seconds() >= settings.max_seconds


async def run_rules(state: IntakeState, ports: IntakePorts, settings: IntakeSettings) -> Update:
    """Run the safety rules on the structured facts; publish `alert.raised` for new rule ids.
    Decides here (not in the router, which cannot write state) whether the caps truncate."""
    now = ports.clock()
    outcome = ports.rules.run(structured_facts(state), now=now, arrived_at=state.get("arrived_at"))
    session_id = state["session_id"]
    known = {alert.rule_id for alert in state.get("alerts", [])}
    for alert in outcome.alerts:
        if alert.rule_id in known:
            continue
        known.add(alert.rule_id)
        data = AlertRaisedData(clinic_id=state["clinic_id"], session_id=session_id, alert=alert)
        await ports.events.emit(EventType.alert_raised, data, f"{session_id}:{alert.rule_id.value}")
    update: Update = {
        "alerts": list(outcome.alerts),
        "needed_facts": list(outcome.needed_facts),
        "ruleset_version": outcome.ruleset_version,
        "node_trace": ["run_rules"],
    }
    if not state.get("conversation_complete", False) and caps_reached(state, now, settings):
        update["flags"] = {"intake_truncated": True}
    return update


def route_after_rules(state: IntakeState) -> Literal["ask", "suggest_urgency"]:
    """Back to `ask` while the conversation goes on; to the suggestion when it is complete or
    truncated by the caps."""
    done = state.get("conversation_complete", False) or state.get("flags", {}).get(
        "intake_truncated", False
    )
    return "suggest_urgency" if done else "ask"
