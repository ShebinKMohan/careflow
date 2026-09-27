# ADR-020: LangGraph intake graph, ports and checkpointing

## Status

Accepted, 27 Sep 2026 ([M1-05]).

## Context

M1-05 builds the intake orchestrator: a LangGraph `StateGraph` with a Postgres checkpointer. It must enforce two safety rules by its shape:

- **Rule 1:** the rules engine runs before any suggestion.
- **Rule 3:** a nurse confirms every level through an `interrupt()`.

Every node calls an injected port, and deterministic stubs are enough for now.

Four things were checked in the pinned LangGraph 1.2.12 (langgraph-checkpoint 4.2.0) before relying on them:

1. **How interrupts surface.** `ainvoke` returns the state plus `__interrupt__`, a list of `Interrupt(value, id)`. `aget_state()` exposes `.next`, `.interrupts` and `.tasks`.
2. **What happens when a node raises after its interrupt.** The stored resume value is replayed on every later resume, so the thread is stuck. This also happens when resuming by interrupt id. The documented alternative works: the node calls `interrupt()` again in a validation loop.
3. **What `aupdate_state` does to a paused graph.** The pause stays at the same node, with no recorded interrupt, and `Command(resume=...)` still works. After a node pauses a second time, `next` is empty but the task keeps its interrupt.
4. **How checkpoints are deserialized.** The msgpack serializer warns about unregistered types, and blocks them under `LANGGRAPH_STRICT_MSGPACK`. An explicit `allowed_msgpack_modules` list matches exact `(module, class)` pairs.

## Decision

### Graph

The nodes and edges follow the issue:

- `START → identify → fetch_ehr_context → ask`.
- `ask` either asks, going to `await_answer → extract → run_rules`, or marks the conversation complete and goes to `run_rules`. (Amended by ADR-041: `intent_router` now sits between `await_answer` and `extract`, and `run_rules → handle_side_question → run_rules` handles a question the patient asks.)
- `run_rules` routes back to `ask` or on to `suggest_urgency`.
- `suggest_urgency → nurse_review → assign_token → writeback → build_briefing → END`.

Conditional edges have path maps, so `get_graph()` lists every possible edge. That makes the static reachability test exact.

When the conversation completes, `ask` goes through `run_rules` again rather than straight to the suggestion. So the model always sees the facts the rules evaluated last, including facts that arrived while the graph was waiting. A test asserts this.

### State (`IntakeState`)

The issue's fields are kept, with a few additions:

- **New fields:**
  - `language` (StructuredFacts needs it);
  - `started_at` (for the time cap);
  - `conversation_complete`;
  - `suggestion_unavailable` (the reason when there is no suggestion).
- **Reducers:**
  - turns and the node trace append;
  - facts merge by fact id;
  - alerts merge by rule id and are never retracted;
  - flags only ever turn on.
- **Facts: `FactEntry`.** The contracts have no keyed fact type: a fact's key is its position in `StructuredFacts`. The state therefore holds `FactEntry {key: FactKey, fact}`, where `fact` is validated as the contract class for that key. For example, `vitals.spo2` holds an `Spo2Fact`, `symptoms` holds a `Symptom`, and `ehr_context.allergies` holds a `CodeFact`.
- **Building `StructuredFacts`.** `to_structured_facts` builds the contract object for the rules and the model. List keys keep every entry. A single-value key keeps one entry: a nurse-confirmed fact first, then the latest recorded, then the later one in the list. For vitals this matches `careflow_rules.merge_vitals` (ADR-016).

### Ports

The port protocols are `Identifier`, `EhrReader`, `Conversation`, `Extractor`, `RulesRunner`, `UrgencyModel`, `QueueService`, `Writeback`, `BriefingBuilder` and `EventSink`, all collected in `IntakePorts`. `IntakePorts` also carries an injected `clock`, which keeps the tests deterministic.

- **`Extractor.extract`** takes `(turns, existing_facts, question_keys)` and returns `ExtractionResult`. Its fields are `facts`, `dropped_count`, `extraction_incomplete`, `error_code`, `prompt_version`, `model_id` and `provider`. The signature is the one the M1-06 issue expects. Fact ids should be stable for the same statement, so a re-extraction replaces a fact rather than duplicating it.
- **`Conversation.next_question(state)`** returns a `Question`: `question_id`, `text`, `key`, and `source` (`llm` or `template`, as in M1-07).
- **`RulesRunner.run`** takes `StructuredFacts`, `now` and `arrived_at`.
- **`UrgencyModel.predict`** has the M1-04 signature. It raises `NoSuggestion` when it cannot suggest; M1-07 maps the triage `OutOfScopeError` to it.
- **Sync and async.** The pure CPU ports (rules, model) are synchronous. The I/O ports are async.

### Caps

The conversation stops at 8 patient answers or 120 s from `started_at` (`IntakeSettings`).

A LangGraph router cannot write state, so `run_rules` makes the cap decision and sets `intake_truncated`. `route_after_rules` only reads the state. That keeps the flag and the route consistent even when the clock moves between the two.

### Interrupts

`await_answer` and `nurse_review` do nothing with side effects before `interrupt()`.

- **Invalid input pauses again.** If an invalid value reaches the node (an empty answer, or a decision that fails the rules below), the node calls `interrupt()` again with an `error`. It never raises there: raising would replay the invalid value on every resume and strand the session.
- **The helpers validate first.** The public helpers `answer()` and `decide()` validate before resuming, and raise `InvalidAnswer` or `InvalidNurseDecision` without touching the checkpoint. So "override without a reason raises, and a later valid resume still succeeds" holds.
- **Decision rules.** A decision must:
  - be a contract `NurseDecision` for this session and the current suggestion, on the configured scale;
  - have an `override_reason_code` on OVERRIDE (`check_nurse_decision`);
  - have a reason whenever `final_level` differs from the suggestion;
  - have a non-empty note when the reason is `other`.

### Fail-safe steps

These failures never stop the intake:

- **EHR.** An error, or no identified patient, sets `ehr_unavailable`.
- **Extraction.** An error sets `extraction_incomplete`, and the rules still run.
- **Urgency model.** A `NoSuggestion`, an error, a suggestion for another session, or a suggestion citing facts that are not in the session (`check_suggestion_evidence`) leaves the nurse to set the level without a suggestion.

The rules engine is not fail-safe: if it raises, the run stops before any suggestion.

### Events

`EventSink.emit(type, data, idempotency_key)` receives the typed data from the M0-03 event contracts; M1-11 wraps it in an envelope and an outbox. The idempotency keys are:

| Event | Key | When |
|---|---|---|
| `alert.raised` | `{session_id}:{rule_id}` | only for rule ids new to the state |
| `triage.suggested` | `{session_id}:triage.suggested:{suggestion_id}` | |
| `triage.confirmed` | `{session_id}:triage.confirmed:{decision_id}` | |
| `intake.completed` | `{session_id}:intake.completed` | |

### Checkpointing

- **Serializer.** `JsonPlusSerializer(allowed_msgpack_modules=state_types())` is an explicit allowlist of every model and enum in the contract and intake modules the state uses (60 classes). Any other type in a checkpoint is not rebuilt.
- **Postgres.** Postgres uses `AsyncPostgresSaver.from_conn_string(url, serde=...)`, and `setup_postgres()` runs `saver.setup()` once. `setup()` needs DDL rights, so production runs it with the migrator role (M1-12).
- **Tests.** Unit tests use `InMemorySaver` with the same serializer.
- **The restart test** uses its own `careflow_intake_test` database, not `careflow_test`, because the API suite drops and recreates `careflow_test` in the same pytest run. Like the API tests, it is skipped without Postgres unless `CAREFLOW_REQUIRE_DB=1` (CI).

### Dependencies

- `langgraph==1.2.12` (already locked through `packages/providers`), `langgraph-checkpoint-postgres>=3.1.2,<3.2` and `psycopg[binary,pool]>=3.3,<3.4`.
- LangSmith comes in with `langchain-core`, but no tracing variable is set.
- No LangGraph Platform, no LiteLLM, and no LLM call in this issue.

## Consequences

- **M1-06 and M1-07** implement `Extractor` and `Conversation` (LLM) and wire real `RulesRunner` and `UrgencyModel` adapters. The rules adapter wraps `careflow_rules.evaluate`. The model adapter wraps `careflow_triage` `lr` and maps `OutOfScopeError` to `NoSuggestion`.
- **M1-11** implements `EventSink` as the transactional outbox, which honours the idempotency keys.
- **M1-12:**
  - drives the graph through `start_intake`, `answer`, `decide` and `add_facts`;
  - maps `InvalidAnswer`, `InvalidNurseDecision` and `NotWaiting` to problem+json;
  - decides where the checkpoint tables live and which roles can read them. They hold session state.
- **Late facts.** Facts that arrive while the graph waits at `nurse_review` are stored, but the graph does not re-run the rules for them. The API must evaluate the rules on new vitals directly, so the nurse is alerted at once (M1-12).
- **Future LangGraph changes.** If LangGraph changes how a node that raises after `interrupt()` resumes, the validation loop still works. The helpers' pre-validation stays the primary path.
