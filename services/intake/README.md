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

## Fact extraction (M1-06)

`careflow_intake.extraction.LlmExtractor` implements the `Extractor` port over the provider layer's `extraction` task. By default this is the local Qwen3.5-9B on `mlx_lm.server`, and `config/providers.yaml` can switch it.

**The prompt.** `prompts/extraction_v1.md` shows the model the transcript as `t1..tn`, with each turn's role and question key, and lets it use only the keys a patient can state.

**Verification.** Code checks every fact, and a fact that fails any check is dropped and logged (`extraction.fact_dropped`, without the patient's words):

- the quote must come from a patient turn;
- it matches exactly, or ignoring case and spacing, and the offsets are always recomputed;
- every number must appear in the quote.

**Failures.** An invalid reply is retried once. A second invalid reply, a timeout or a provider error returns the facts already known, with `extraction_incomplete`.

```bash
uv run pytest services/intake -k extraction                      # replayed replies (M0-06 ReplayProvider)
CAREFLOW_LIVE_LLM=1 scripts/with_llm.sh uv run pytest services/intake -m live -s
scripts/with_llm.sh uv run python -m careflow_intake.extraction.record --case live/chest_pain
```

## The conversation and the real ports (M1-07)

`careflow_intake.conversation.ChecklistConversation` implements the `Conversation` port. Code decides what to ask, and the LLM only rephrases:

- **The picker** asks the complaint first. Next come the danger screens the rules ask for, the most severe alert's first, then the rest of the checklist: onset, pain score, severity, pain location, allergies.
- **After a critical alert,** the fixed nurse-coming message is shown, and only the remaining screens follow.
- **The approved questions** are in `conversation/templates_en.yaml`.
- **Phrasing.** With `llm_phrasing: true` in `config/intake.yaml`, the `conversation` task rephrases a question within 3 s, and anything that is not one clean, allowed question falls back to the template.
- **The output filter.** `conversation.output_filter.check_patient_facing` checks every string sent to the patient, and blocks diagnoses, condition names, advice, dosing, more than one question and anything over 40 words.

`careflow_intake.wiring.build_real_ports()` wires the M1-06 extractor, the rules engine (`CareflowRules`) and this conversation into the graph. The other ports are stubs until their issues land.

```bash
uv run pytest services/intake -k "conversation or scripts"          # scripted patients, recorded replies
scripts/with_llm.sh uv run python -m careflow_intake.scripts --record chest_pain
```

## The doctor briefing (M1-16)

`careflow_intake.briefing.make_briefing(state, now=..., summarizer=...)` builds the briefing after the nurse's decision (ADR-030):

- **`builder`.** Seven fixed sections: alerts and triage, presenting complaint, danger-screen answers, vitals and NEWS2, record (EHR), model output, data quality.
  - Every item comes from a fixed template over one fact (or an alert, the nurse's decision, NEWS2 or the suggestion) and carries its provenance and source.
  - A model-inferred item always shows the nurse's level.
  - Data-quality warnings and empty sections are notes, not items.
- **`check`.** Fails closed: an item whose source cannot be verified in the session is kept, marked `source_missing` and logged as an error.
- **`summary`.** Optional (`briefing_summary` in `config/intake.yaml`). The `summary` task writes at most 5 sentences that cite fact ids, and `check_summary` drops the whole summary if any sentence cites nothing or an unknown fact, adds a number, or reads as a diagnosis or advice. A missing or unreachable provider disables it. The plain list is always complete.

The API stores it and writes the FHIR document (`careflow_api.briefing`); `careflow_intake.wiring.briefing_summarizer()` builds the summarizer from the config.

```bash
uv run pytest services/intake -k briefing                                    # the 7 personas, replayed summaries
uv run python services/intake/tests/fixtures/briefing/generate.py           # rebuild the persona states
scripts/with_llm.sh uv run python -m careflow_intake.briefing.record --persona all   # record the summaries
```

## Tests

```bash
uv run pytest services/intake    # the Postgres restart test runs when `make up` (or the CI container) is available
```

Decisions are in ADR-020 and ADR-030.

Research prototype. Synthetic data only. Not for clinical use.
