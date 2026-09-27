# ADR-041: Questions the patient asks mid-intake (the intent router)

## Status

Accepted, 27 Sep 2026 ([M2-10]). This is the core build. The scripted conversations with inserted interruptions, their recorded model outputs, the `-k intent_router` tests and the scripted-clock time budget follow in the test phase.

## Context

Real patients interrupt: "Where's the toilet?", "Is this serious?". Before this change, the intake treated every message as an answer:

- A question was passed to the extractor as the answer to the question on screen.
- The patient got no reply to it.

The router must keep three safety properties:

- **Rule 1.** Symptoms inside a question must still become facts and alerts.
- **Rule 2.** A medical question must never be answered.
- **The graph invariant.** `run_rules` stays the only way to `suggest_urgency`, and nothing skips `nurse_review` (ADR-020).

## Decision

### The graph

```
ask -> await_answer -> intent_router -> extract -> run_rules
run_rules -> handle_side_question -> run_rules      (a side question waits for its reply)
run_rules -> ask | suggest_urgency                  (route_after_rules, as before)
```

- **`intent_router`.** It only records the turn's `TurnIntent`, a new contract with three flags: `intake_answer`, `clinic_question` and `medical_question`. Several can be true at once. It never calls `interrupt()` and never keeps a turn from `extract` and `run_rules`.
  - A tapped choice is always an answer.
  - A turn that is only a question does not count towards the 8-answer cap, though the 120 s timer keeps running.
  - Its turn id is kept (`side_turn_ids`), so the extractor is not told that it answered the question on screen. Symptoms in its text are still extracted.
- **`handle_side_question`.** It runs after the rules, so an alert never waits for a side question. It returns to `run_rules` instead of routing onwards: `run_rules` stays the only edge into `suggest_urgency`, and the existing ordering tests still hold. The repeated rules run is deterministic and raises no alert twice (alerts go out once per rule id).
  - **A medical question.** It is stored in `nurse_flags` with reason `medical_question_intake` (migration 0011; the flag id comes from the session and turn, so a replayed node adds nothing). `nurse.flag` goes out with idempotency key `{session_id}:{turn_id}:nurse_flag` and no words. The reply is the fixed refusal (`safety.rag.refusal`, M2-09). The flag fires every time, even over the budget.
  - **A clinic question on the phone.** The ask-the-clinic agent (M2-09) answers. The answer is cut to 2 sentences and checked by the output filter; its citations go with it. If the agent refuses, its own flag has already gone out, and the reply is the refusal.
  - **A clinic question on the kiosk.** The reply is the fixed "You can ask this on your phone: tap 'Continue on your phone' and scan the code." The agent is never called on the kiosk (ADR-006).
  - **The budget.** After 2 side questions, the reply is "Let's finish these questions first. You can ask the reception desk afterwards."
- **`ask`.** The reply becomes the next question's notice (before the nurse-coming message, if any), with its kind and citations.
  - A turn that was only a question gets the interrupted question again, word for word. It is not counted as another ask of its key, because each key is asked at most twice (M1-07).
  - It is asked again only while its fact is still missing. The live smoke run showed why: the model called "I have chest pain, is it serious?" only a question, but it had answered "What brought you in?".
- **The fixed texts** are in the approved templates (`messages:` in `templates_en.yaml` and `templates_ar.yaml`) and pass the output filter.

### The classifier (`careflow_api.intake.side_questions.TurnClassifier`)

- **Only questions reach the model.** A turn goes to the `conversation` model only when it reads like a question:
  - a "?" or "؟";
  - a question word (what, where, when, how…, or أين, متى, كيف, كم…);
  - yes/no wording ("is it", "should I", "can I", "do I"…, or "هل").
  A plain answer never reaches the model and is never called a question. This is the issue's latency fallback, used from the start: most turns are plain answers, so they get no extra model call.
- **Why the lexicon cannot decide alone.** Intake answers are full of symptom words. The M2-09 lexicon alone would call "I have chest pain since this morning" a medical question, and refuse and flag it.
- **When the lexicon forces a medical question.** Only on a clear question: a question mark or yes/no wording. An open question word alone ("where my chest is", answering "Where is the pain?") is left to the model.
- **Failures.** A model failure or a 10 s timeout gives the fail-safe intent: an answer, and a medical question (category `fail_safe`). The facts are still extracted, and the nurse is flagged.
- **The prompt** shows the question the patient was answering. It treats the question and the message as data between tags.

### Contracts

- **New:** `TurnIntent` (`turn-intent.schema.json`).
- **Changed:** `nurse.flag` gains the reason `medical_question_intake` (an added enum value).
- **Optional fields on `IntakeQuestion`:**
  - `notice_kind`: `nurse_coming`, `medical_refusal`, `clinic_answer`, `ask_on_phone` or `finish_first`;
  - `citations`.
- **The turns route.** It returns the notice, a blank line and the question in `assistant_text`. When the intake ends on a side question, the reply comes before the done text.
- oasdiff reports no breaking change.

### The kiosk

- `ask_on_phone` fills the "Continue on your phone" button.
- The voice client keeps `notice_kind`, so this also works after a spoken turn.

## Consequences

- **On the local stack** (smoke run with local Qwen3.5-9B):
  - **The kiosk:** "Where is the toilet?" got "ask this on your phone" and the same question again. "I have chest pain, is it serious?" recorded the complaint and chest pain, raised `CHEST_PAIN_ECG_10MIN`, and flagged the nurse once. That turn's reply showed the refusal, then the nurse-coming message, then the next chest-pain question. A third side question got "let's finish these questions first".
  - **The phone:** "Where is the pharmacy?" got a one-sentence answer citing `pharmacy.hours:0`. An Arabic Panadol question was refused and flagged.
  - **Flags and events:** both flags are on the worklist, and each `nurse.flag` carries no words.
  - **Timing:** turns without a question took about 3 s. Turns with a question took 2 to 11 s, and 17 to 20 s when the phone agent or the embedder first loaded.
- **Pending for the test phase:**
  - `pytest services/intake -k intent_router` on 10 scripted conversations (5 English, 5 Arabic) with 1 to 3 interruptions each, with recorded model outputs;
  - the facts diff with and without the interruptions;
  - the exact re-ask;
  - no agent calls on the kiosk;
  - the mixed chest-pain turn;
  - a classifier failure;
  - the scripted-clock budget: conversations with at most 2 interruptions finish in 120 s or less.
