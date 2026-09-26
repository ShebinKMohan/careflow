# ADR-021: LLM fact extraction with code-verified spans

## Status

Accepted, 27 Sep 2026 ([M1-06]).

## Context

Free talk becomes facts through an LLM: the local Qwen3.5-9B 4-bit by default, through the M0-06 provider layer. An LLM can invent a symptom or miscount characters. The doctor briefing (M1-16) needs every patient-stated fact to point at the patient's own words (safety rule 4). The M1-05 graph already fails safe on `extraction_incomplete`.

## Decision

### Contract

- **New schema.** `packages/contracts/schemas/extraction-output.schema.json` defines what the model returns: `ExtractionOutput {facts: ExtractedFact[]}`.
- **`ExtractedFact` fields:**
  - `key`: a `FactKey`, so no free-form keys are allowed;
  - `value`: bool, int, float or string;
  - `unit`, `turn_id` and `quote`;
  - optional `char_start` and `char_end`, which are ignored;
  - `negated`, default false.
- **Fixtures and tests.** Adding the schema is additive, and the contract tests now list six schemas. The schema has a valid fixture and an invalid one (a free-form `diagnosis` key).

### Prompt

`extraction/prompts/extraction_v1.md` (`PROMPT_VERSION = "extraction_v1"`):

- **Layout.** A system message holds the rules and the allowed keys. The user message is the transcript as `t1..tn [role, asks about|answers <key>]: text`.
- **Rules:**
  - copy the quote verbatim from a patient turn;
  - extract everything the patient states, not only the answer to the question just asked;
  - a bare "yes" or "no" answers the question's key;
  - yes/no values are true or false, never the patient's words;
  - denials become false with `negated`;
  - numbers are given as said;
  - never infer a diagnosis;
  - reply with JSON only.
- **One worked example.** The prompt ends with a short example conversation and its reply, on a scenario that is not among the test transcripts (a cough with fever, and a hand hurt by a car door).
  - The first live runs showed the need. The 9B model left `infection.fever_reported` out of "I've had a high fever", and it put the sentence "Yes, I fell on it." as the value of `injury`, which the verifier rightly dropped. Rules alone did not change this.
  - The example teaches the two behaviours. The M1-18 eval suite measures whether they generalise.
- **Allowed keys** (45): yes/no answers, whole numbers (age, pain score), free text and symptoms.
- **Excluded keys:**
  - Vital signs come from devices and nurses. A patient-stated temperature is not a measurement, and "on oxygen" said at home is not NEWS2's "on oxygen".
  - Coded keys (sex, arrival mode, complaint category), EHR keys and model-inferred keys come from other sources.

  A fact with an excluded key is dropped even if the model returns one.

### Calling the model

- **Provider layer only.** The extractor calls the model through `build_provider("extraction", config)` and `structured(provider, messages, ExtractionOutput, temperature=0)`. The call runs under a 20 s total budget (`asyncio.wait_for`, covering both attempts).
- **Retry.** `structured()` validates with Pydantic and retries once with the errors. The provider layer now cuts those errors to 2,000 characters (`MAX_ERROR_CHARS`), for every task.
- **Switching providers.** `LlmExtractor.from_config()` builds whatever `providers.yaml` sets for `extraction`, so a switch to `provider: api` needs no code change. A test covers this with a fake LangChain chat model.

### Verification in code (`verify.py`)

- **Turn and role.** The cited turn, by label `tN` or by turn id, must exist and be a patient turn.
- **Finding the quote.** The quote is found:
  1. exactly;
  2. then ignoring case and runs of whitespace;
  3. then also ignoring punctuation at the quote's ends.

  The offsets are always recomputed. The stored quote is the exact slice of the patient's turn, so `text[start:end] == quote` for every fact.
- **Numbers.** A number must appear in its quote as digits (including a decimal comma) or as a word from zero to ten.
- **Other drops.** A value that does not fit the key is dropped:
  - a yes/no answer that is true but also negated (contradictory: the question can be asked again);
  - a non-integer pain score;
  - anything outside the contract's range.
- **Logging.** Every drop is logged as `extraction.fact_dropped` with the key, the turn label and the reason, never the patient's words, and counted in `dropped_count`.

### Facts

- **The facts.** Each fact is a `PATIENT_STATED` `FactEntry` with a `transcript_span` source (turn id, start, end, quote), recorded at the turn's time.
- **Ids.** Fact ids are stable: derived from the turn id and time and the key, plus the span start for list keys such as symptoms. Extracting the whole transcript again after each answer therefore replaces facts instead of duplicating them.
- **Same key.** For the same key, the latest patient statement wins.

### Failing safe

These return the facts already known, unchanged, with `extraction_incomplete=true`:

| Failure | `error_code` |
|---|---|
| A second invalid reply | `invalid_output` |
| A timeout | `timeout` |
| Any provider error (server down, no API key, a replay miss) | `provider_error` |

The M1-05 graph then flags the session, and the rules still run.

### Replay and recording

- **One mechanism.** `RecordedProvider` is M0-06's `ReplayProvider`; there is no second replay mechanism.
- **Recorded cases.** A case recorded from a real model replays its cassette. The cassette is keyed by the exact messages, so a prompt change makes the replay miss and the case must be recorded again.
- **Scripted cases** (hand-written replies for the failure paths) record their replies into a temporary cassette as they are served.
- **Recording tool.** `python -m careflow_intake.extraction.record --case <name>` records through `scripts/with_llm.sh`. It applies `CAREFLOW_LOCAL_LLM_BASE_URL` to the config.
- **Live tests.** The live test is marked `live` and needs `CAREFLOW_LIVE_LLM=1`.
- **Warm-up.** `record.py` and the live test first send one untimed request that starts with the extraction system prompt and its schema. The server caches that shared prefix.
  - On a fresh server this warm-up took 8.2 s. The first extraction after it took 10.6 s, against 18.5 s after a one-word warm-up and a 20 s timeout with none. Later extractions took 7.5-7.6 s.
  - The warm-up time is reported next to the p50, not hidden.
  - M1-12 should send the same warm-up when the API starts.

## Consequences

- **M1-07** wires `LlmExtractor.from_config()` into `build_real_ports()`. The graph's extract node already passes the whole transcript and the question keys.
- **M1-18** can reuse the recorded and scripted fixtures for the extraction F1 suite.
- **Prompt changes** require re-recording the live cassettes. The replay tests fail loudly until that is done.
- **Vital signs** stated by a patient are not extracted. If a later issue wants them, they need their own provenance handling in the rules.
