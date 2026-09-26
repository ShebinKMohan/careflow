# ADR-022: The intake conversation, the output filter and the real-port wiring

## Status

Accepted, 27 Sep 2026 ([M1-07]).

## Context

The M1-05 graph asks the patient questions through a `Conversation` port. M1-07 needs:

- a predictable intake: a fixed checklist, plus the danger screens the rules ask for;
- an LLM that only phrases questions;
- nothing diagnostic, and no advice, reaching the patient (safety rule 2);
- the real extractor (M1-06) and the real rules (M1-01/M1-02) wired into the graph.

The issue names checklist keys (`symptom_onset_at`, `severity`, `pain_location`, `allergies_confirm`) that were not M0-02 fact keys, and it asks for question keys to match the fact keys.

## Decision

### Contract

`StructuredFacts` gains four checklist facts, each with a `FactKey` value, a Questionnaire item and a QuestionnaireResponse mapping row:

| Fact | Type |
|---|---|
| `symptom_onset_at` | text, in the patient's words (not parsed to a date) |
| `severity` | `SeverityFact`, 0-10 |
| `pain_location` | text |
| `allergies_confirm` | text |

- **Why `allergies_confirm` is free text.** Its two questions give "yes" opposite meanings: "Your record lists X. Is that still correct?" against "Are you allergic to any medicines?". A yes/no fact would store a trap. The coded list stays in `ehr_context.allergies`.
- **oasdiff.** `FactKey` grows, so oasdiff reports 16 reviewed enum additions (2 routes × 2 response properties × 4 values). They are listed under "M1-07 (ADR-022)" in `oasdiff-err-ignore.txt`. The M1-04 lines, now on main, are removed.

### Templates

`conversation/templates_en.yaml` (`templates_en_v1`) holds one approved plain-English question for each of 23 keys:

- the checklist;
- the FAST and chest-pain screens;
- fever and rigors;
- the six NG143 parent-screen questions.

It also holds the fixed `nurse_coming` message. The allergy question names the EHR allergies when there are any. If that wording fails the filter, the open question is used instead.

### The picker (`pick_next_question`)

- **Order.**
  1. The complaint first.
  2. The patient-audience screens the rules asked for, the screen of the most severe raised alert first, then in rule order.
  3. The rest of the checklist, in order: onset, pain score, severity, pain location, allergies.
- **Asks.** A key is asked at most twice: the question, plus one re-ask when the answer gave no fact.
- **Skips.** The checklist skips questions the answers already make pointless:
  - the onset, when a chest-pain onset or last-known-well time is known;
  - the pain score, when the patient has no pain;
  - the severity, when a pain score is known (it is the severity);
  - the pain location, for chest pain or when there is no pain.
- **After a critical alert**, only the remaining screens are asked.

### Nurse coming

After a critical alert, the next patient-facing text is the fixed message.

- **With screens left.** The message comes as the question's `notice`. `Question.notice` is new, and the `ask` node records it as its own assistant turn before the question.
- **No screens left.** The message comes alone as a question with no key, which the patient acknowledges ("OK"), and the conversation ends.
- **Once per session.** It is shown once. The nurse is already coming for any later critical alert.

### Phrasing (`Phraser`)

- **The call.** Task `conversation`, with the issue's prompt, `max_tokens` 80 and a 3 s budget (`config/intake.yaml`).
- **What is accepted.** Only exactly one question, ending in "?", of at most 40 words, that passes the filter. Anything else, or an error or timeout, uses the template.
- **Templates only.** `llm_phrasing: false` in `config/intake.yaml` never calls the model. This is deterministic, for E2E and the scripts.

### The output filter (`check_patient_facing`)

Every string sent to the patient passes through it, including the templates and the nurse-coming message. It blocks:

- **Diagnosis statements.** The "you have" pattern needs a hedge word ("you probably/likely/may/might/could/must have"), so "Do you have chest pain?" stays allowed. Also blocked: "you are having a/an", `diagnos` and "it/this/that sounds/looks/seems like".
- **Condition names:** heart attack, stroke, sepsis/septic, infection(s)/infected, cancer, tumour, pneumonia, appendicitis, meningitis, fracture(d), blood clot, embolism, angina and concussion.
- **Advice:** you should, I (would) recommend, don't worry (including the curly apostrophe), nothing serious, go home.
- **Dosing:** `\d+ (mg|ml|tablets)`.
- **Other:** more than one "?", more than 40 words, and empty text.

Drug names are not blocked, because the allergy question names the patient's allergies. Dosing and advice patterns cover the risk instead.

The fixtures cover the filter: 33 of 33 adversarial outputs blocked, 23 of 23 good questions allowed, and 25 of 25 template texts pass.

### Wiring (`build_real_ports`)

- **Real ports:**
  - `LlmExtractor`, built from `providers.yaml`, or injected (for example a replay);
  - `CareflowRules`, a `RulesRunner` adapter over `careflow_rules.evaluate`;
  - `ChecklistConversation` with its `Phraser`.
- **Still stubs:**
  - patient identity and EHR (M1-08);
  - urgency model (M1-12 loads the M1-04 artifact);
  - queue (M1-10);
  - write-back (M1-09);
  - briefing (M1-16);
  - events (M1-11).
- **Settings.** `providers_config()` points every local task at `CAREFLOW_LOCAL_LLM_BASE_URL` when `scripts/with_llm.sh` sets it. `config/intake.yaml` also sets the turn and time caps.

### Extraction prompt `extraction_v2`

- **Why it changed.** The four new keys changed the extraction prompt: its key list and the schema's `FactKey` enum. The live model then filed "a high fever and chills" under `symptoms`, and did not extract the allergy answer "No, none that I know of".
- **What v2 adds:**
  - use the specific key (fever, rigors, chest pain) rather than `symptoms`;
  - the answer to a free-text key's question is that key, in the patient's words, even when it is "no" or "none";
  - the worked example gains an onset and an allergy answer.
- **Versioning.** `PROMPT_VERSION` becomes `extraction_v2`; v1 stays in the repo for traceability. All live cassettes were recorded again.
- **A yes/no for a free-text key.** Even with v2, the model answered the allergy question with `false` / negated, a yes/no for a free-text key, and the verifier dropped it. The extractor now keeps the patient's verified quote as the value of a free-text key whenever the model's value is not text. That is their exact words, with no inference. The scripted fixture `text_answer_as_yes_no` covers it.

### Scripts

`tests/fixtures/scripts/*.yaml` hold synthetic patients: chest pain, sepsis with the M1-03 septic kiosk readings, and a sprained ankle. Each has answers by question key, device facts and expectations.

- **Recording.** `python -m careflow_intake.scripts --record <name>` runs the live model through `scripts/with_llm.sh`, recording every extraction reply.
- **Tests.** The tests replay them through the real rules and the template-only conversation.

## Consequences

- **M1-12** builds the API on `build_real_ports()`, adds the real urgency adapter, and should warm the model at startup (ADR-021).
- **M1-13's kiosk** shows `notice` before the question, and an OK button for a key-less question.
- **M2-03** adds `templates_ar.yaml`, and the filter needs Arabic patterns.
- **M2-10** handles questions the patient asks mid-intake.
- **M1-18** reuses the scripts for the eval suite.
- **Re-recording.** Any change to the extraction prompt or to `FactKey` changes the recorded prompts. The live and script cassettes must then be recorded again: their replay tests fail until they are.
