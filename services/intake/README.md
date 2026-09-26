# careflow-intake

The CareFlow intake graph (M1-05) is a LangGraph 1.2 `StateGraph` that runs one patient's intake. It uses a Postgres checkpointer, and the thread id is the session id.

```
START -> identify -> fetch_ehr_context -> ask
ask -> await_answer -> extract -> run_rules          (a question was asked)
ask -> run_rules                                     (the conversation is complete)
run_rules -> ask | suggest_urgency                   (route_after_rules)
suggest_urgency -> nurse_review -> assign_token -> writeback -> build_briefing -> END
```

The graph's shape enforces two safety rules:

- **Rule 1.** `suggest_urgency` is reachable only through `run_rules`, and the model sees exactly the facts the rules last evaluated.
- **Rule 3.** `nurse_review` pauses with `interrupt()`, and no level is final until a nurse resumes the graph.

The tests prove both.

## Using it

```python
from careflow_intake.checkpoint import postgres_checkpointer, setup_postgres
from careflow_intake.graph import add_facts, answer, build_graph, decide, pending, start_intake

# Once at startup: create the checkpoint tables.
await setup_postgres(migrator_url)
async with postgres_checkpointer(database_url) as saver:
    graph = build_graph(ports, saver)  # ports: IntakePorts (careflow_intake.ports)
    result = await start_intake(graph, session_id=sid, clinic_id=cid, arrived_at=now)
    # pending(result) -> {"kind": "patient_input", "question": {...}}
    result = await answer(graph, sid, "Tight chest since this morning")
    # External facts, merged by fact id.
    await add_facts(graph, sid, [device_reading])
    ...
    # pending(result) -> {"kind": "nurse_review", "suggestion": ..., "alerts": [...]}
    # A contract NurseDecision; the graph then ends with a token and a room.
    result = await decide(graph, sid, nurse_decision)
```

**Pauses.** The graph pauses in two places: `patient_input`, for each question, and `nurse_review`.

**Input checks.** `answer()` and `decide()` check the input before resuming:

- `InvalidAnswer` is raised for an empty or too-long answer.
- `InvalidNurseDecision` is raised for a bad decision. OVERRIDE, or any level other than the suggestion, needs a reason code, and the code "other" also needs a note.
- In both cases the graph keeps waiting, so a later valid input goes through.
- If an invalid value reaches the graph another way, the node pauses again with an `error` instead of failing.

**Conversation caps.** The conversation stops after 8 patient answers or 120 s, and sets the `intake_truncated` flag (`IntakeSettings`).

**Failures that don't stop the intake.**

- An unavailable EHR sets `ehr_unavailable`.
- A failed extraction sets `extraction_incomplete`, and the rules still run.
- A model that cannot suggest (`NoSuggestion`, e.g. a child for the adult-only KTAS model) leaves the suggestion empty with a reason, and the nurse sets the level.

**Events.** Every event is published through `EventSink.emit` with an idempotency key:

- `alert.raised`: once per rule id.
- `triage.suggested`
- `triage.confirmed`
- `intake.completed`

**Stubs.** `careflow_intake.stubs` holds deterministic stand-ins for every port (`stub_ports()`), for tests and demos. M1-06, M1-07 and M1-12 inject the real ones.

## Tests

```bash
uv run pytest services/intake    # the Postgres restart test runs when `make up` (or the CI container) is available
```

Decisions are in ADR-020.

Research prototype. Synthetic data only. Not for clinical use.
