# ADR-040: The phone-only "ask the clinic" agent and its medical refusal

## Status

Accepted, 27 Sep 2026 ([M2-09]). This is the core build. The agent's pytest cases (about 40 hand-written questions, replayed with a recorded provider), the 20 medical-question refusals, the 403 cases and the event schema test follow in the test phase.

The test phase is done (SHE-306, docs/evidence/M2-09.md). It added an output gate on every generated answer and set the "I don't know" threshold from the M2-08 eval: ADR-054.

## Context

Most questions come during the wait. Answers from the clinic's own handbook can be checked, because they cite it. Medical questions must never be answered: safety rule 2 says no AI output reaches a patient as medical advice.

- The contract already had `POST /v1/companion/ask` (M0-03) and the `nurse.flag` event.
- M2-08 built the handbook retrieval, and M2-06 built `require_phone_session()`.
- ADR-006 keeps this on the phone only: no kiosk RAG or wayfinding.

## Decision

### A fixed-shape graph (`careflow_rag.agent`)

An explicit LangGraph `StateGraph`, never a free ReAct loop:

```
classify -> medical -> refuse_and_flag -> END
         -> clinic  -> plan -> run_tools -> answer -> check_citations -> END
         -> other   -> "I can help with questions about the clinic." -> END
```

- The graph is compiled per question, with the tools bound to the verified token. It needs no checkpointer: a question is one short run.
- A hijacked model can only choose among these edges. It cannot call a tool with its own arguments, and it cannot reach a patient with medical content.

### The medical classifier fails safe

A question is medical if either layer says so.

1. **Layer A, the lexicon** (`careflow_rag/medical_lexicon.yaml`). It is checked first; a match skips the model entirely.
   - **English:** regular expressions matched as whole words. They cover symptoms, body parts with pain words, medicine and drug names, doses, advice phrasings ("should I take", "is it serious", "can I eat"), tests and results, and mental health.
   - **Arabic:** words compared after normalisation (no diacritics or tatweel; the alef, alef maqsura, ta marbuta and hamza seats folded), with one common prefix and one common suffix removed, plus whole-word phrases.
   - **No substring matching.** The normalised "ألم" (pain) is inside "الماء" (the water).
   - **Both languages always.** Both lexicons run on every question, whatever the session language.
   - **Kept out on purpose:** everyday logistics words such as "prescription", "pharmacy", "emergency", "patient"/"مريض", "scan" (the QR code), "tablet" (a device) and the verb "treat".
2. **Layer B, the model.** `structured(provider, messages, IntentVerdict)` on the `conversation` task (local Qwen3.5-9B). The prompt treats the question as data and says "when unsure, reply `medical`".
3. **Fail safe.** A `StructuredFailure`, a timeout (`llm_timeout_s`, 20 s) or any exception counts as medical. The provider is built on the first question, so a missing provider refuses questions instead of stopping the API. The flag records which layer decided: `lexicon`, `model` or `fail_safe`.

### Refusal and flag

- **The answer.** A medical question gets the fixed text `safety.rag.refusal`: "I can't answer medical questions. I've told the nurse, who will speak with you." It has an Arabic translation and no medical content. The companion shows its own copy of the same key.
- **The flag.** `nurse_flags` (migration 0010, clinic RLS) stores the question's words (at most 500 characters), its language and the category, for the nurse's worklist. The app may read, insert and delete, never change a flag.
- **The events.** The same transaction adds `nurse.flag` (`reason: medical_question`) and the internal `rag.refused` (`category`) to the outbox. Neither carries the words.
- **If the flag cannot be stored,** the route answers 503 ("Questions are not available right now. Please ask at the reception desk."). The patient is never told the nurse knows when the nurse does not.
- **Deletion.** The rows are deleted when the session's token closes or becomes a no-show, in the queue's transaction. The grace-window loop also deletes the rows of sessions that have left the worklist's 24-hour window.

### Answering a clinic question

- **`plan`.** `structured(..., ToolPlan)` chooses among `retrieve`, `queue_position` and `wait_time`. A failed or empty plan falls back to `retrieve`.
- **Tools take no model arguments.** Retrieval uses the patient's question as typed. The queue tools use the token's session; the API's port refuses any other id.
- **`answer`.**
  - If only retrieval ran and the best dense cosine is below `min_score`, the result is "I don't know" with no answer call.
  - Otherwise `structured(..., ClinicAnswer)` gets only the retrieved chunks and the queue data, quoted as data with their ids, plus the last answered turns of the session. The prompt asks for at most 3 short sentences in the session language.
- **`check_citations`.** Every cited id must be a retrieved section, or `live:queue` when a queue tool ran. An answer that cites nothing is not a cited answer. Either failure retries the answer once, with a note, then gives "I don't know".
- **Citations.** Section citations come from M2-08's `citation()`. Its `chunk_id` is now `<section>:<chunk>`, because the API's Identifier pattern rejects `#` (this amends ADR-039). `live:queue` gets a localised title.

### `config/rag.yaml` (provisional values)

- **Mode: `dense`, k = 5.** On a 14-question scratch check (not the eval), dense put the right section first for 13 and hybrid for 9. Both had it in the top 5 for all 14. Hybrid's weakness here is the small handbook (ADR-039).
- **`min_score` = 0.45.** On a 22-question scratch check (not the eval), clinic questions scored 0.48 to 0.77 and unrelated ones 0.15 to 0.43.
- Both values are provisional. The M2-08 retrieval eval's hit rates and cosine distribution replace them in the test phase.

### The API (`careflow_api.ask`, `routers/companion.py`)

- **Access.** `require_phone_session()`: kiosk devices, kiosk-channel tokens and staff get 403; a handed-over kiosk token gets 401 (ADR-037).
- **One `AskService` per process.** It holds one agent and one embedder, loaded on first use: about 15 s the first time on this Mac.
- **Embedding.** The embedding runs in a worker thread, with one encode at a time, so it never blocks the event loop.
- **Memory.** The last 3 answered turns per session are kept in memory only, for up to 1,000 sessions. Refused questions are never kept.
- **Logs.** The route logs the outcome, category and model-call count, never the question.

### The nurse station

- **The worklist item.** It gains `medical_questions` (newest first, at most 20): the words, the language and the time.
- **The panel.** The staff app shows them in a region of their own, as a patient-words panel, not an alarm: no sound, vibration or pulse.
- **Refresh.** The live worklist now refetches on `nurse.*` frames.

## Consequences

- A waiting patient can ask practical questions in English or Arabic and get short cited answers or "I don't know". A medical question reaches the nurse, not the model's answer.
- **On the local stack (smoke run; 13 questions, local Qwen3.5-9B and Qwen3-Embedding):**
  - **Access:** the kiosk device and a nurse got 403; the handed-over kiosk token got 401.
  - **Handbook answers:** "Where is the pharmacy?", its holiday hours, parking, and the Arabic pharmacy and visiting-hours questions got correct answers citing `pharmacy.hours:0`, `parking.car_park:0` and `visitors.hours:0`.
  - **Queue answers:** "How long is my wait?" gave the patient's own position and estimate, citing `live:queue`. A question that named another session id still got the patient's own position.
  - **Refusals:** the paracetamol injection, "Which ward treats my chest pain?" and the Arabic Panadol question were refused by the lexicon; "My son looks pale and sleepy" was refused by the model.
  - **Flags:** four flags and four `nurse.flag` + `rag.refused` pairs were written, with no question text in either event. The worklist showed the four questions.
  - **Other:** unrelated questions got "I can help with questions about the clinic."
  - **Times:** handbook answers took 4 to 7 s (21 s for the first, while the embedder loaded); lexicon refusals took under 0.1 s.
  - **Without the embeddings extra** (it was missing in the first run), every handbook question safely gave "I don't know".
- **Pending for the test phase:**
  - `pytest services/rag -k agent` on fixtures with a recorded provider: valid citations, an invented id giving "I don't know" after one retry, and a low score giving "I don't know" with no answer call;
  - 20 medical questions in English and Arabic, each with the exact refusal and exactly one `nurse.flag`;
  - a provider that raises gives `refused_medical`;
  - 403 for `kiosk_device`, `channel=kiosk` and nurse tokens, and a phone token seeing only its own queue;
  - the `nurse.flag` schema test with no free text;
  - the companion and server refusal texts are identical;
  - the flags are deleted on close and no-show.
