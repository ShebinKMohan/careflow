# ADR-055: Arabic scenarios, their back-translation review, and the per-language extraction report

Status: accepted, 28 September 2026 (SHE-290 [M2-03], the test phase of ADR-034)

## Context

ADR-034 built the Arabic conversation, the Arabic extraction prompt (`extraction_v2_ar`) and the
Arabic-aware span and number verification. It left pending:
- about 100 Arabic scenarios (50 Gulf, 50 Levantine), judge-checked;
- the per-language harness and report;
- recorded Arabic fixtures and the `-k arabic` tests.

The handoff (27 September) added: use the actual M1-18 package, bind review to exact content and a
different writer and reviewer, preserve failed reviews, record dialect and back-translation
provenance, keep English replay compatible, list missed alerts and incomplete cases explicitly,
and keep Arabic numbers in caveated technical reports only.

## Decision

### Three steps, and who does what

1. **Seeded gold (code).** `careflow_eval.sampler.arabic_plan(dialect, seed=7)` samples, per
   dialect, 50 scenarios in the English corpus's category mix. For each: age, sex, vitals, and an
   ordered list of answers, each with the question it replies to and the gold facts it states.
   A complaint states the facts it names ("Tightness in my chest" is also `chest_pain.present`
   and `pain_location`). `plan_alerts` runs the rules engine on the gold; the writer never sets an
   alert. Every rule id is covered at least 4 times (the issue asks for 3).
2. **Written answers (the coding agent).** The writer (`claude-opus-5-5`, in session, as the
   autonomy defaults allow) put each planned answer into the dialect, with an English gloss:
   `{id: [{text, gloss_en}]}`. `careflow-eval generate --suite intake_ar` merges them into drafts.
   Each turn records `gloss_en` and `states` (the gold keys it states). The loader checks that a
   stated number is in the words (digits, Arabic-Indic digits or number words) and that
   "unknown" is an unsure answer (ما أدري، مش متأكد …); yes/no and free-text renderings rest on the
   review, since English patterns cannot check Arabic.
3. **Review.** Before any judge, every answer passes the M2-01 back-translation check
   (`careflow_eval.backtranslation`): the local Qwen3.5-9B translates the Arabic answer alone back
   to English, judges it against the gloss, and chrF must be ≥ 40. A flagged answer rejects the
   scenario before the judge sees it. Then a judge of another family than the writer:
   - **The local Qwen3.5-9B** first (`eval_local.py review`, one scenario per call). Its
     instructions gain one Arabic paragraph (prompt `independent-scenario-review-v2-ar1`); the
     English prompt is byte-identical.
   - **Codex (`gpt-6-astra`) for what Qwen rejects.** On English, Qwen's rejections were escalated
     to Claude; here Claude wrote the answers, so Codex, `eval_judge`'s configured tool, reviews
     them. `AuditedCodexReviewProvider` keeps the Claude adapter's boundary (offline only, a
     filtered environment, an empty temporary directory, a read-only sandbox, the user's Codex
     config and rules ignored, an explicit model and effort, at most 199 calls). The reviewer's
     identity is read from Codex's own session log for the call (`turn_context.model`, effort and
     sandbox, `session_meta.model_provider`), never its account ids. A CLI review reuses the
     passing back-translation check recorded for the exact content; it never runs the check.

   **Outcome** (all in `eval/datasets/review-log.jsonl` and `review-receipts/`): all 100 approved,
   48 by Qwen and 52 by Codex. On the way, Qwen's judge rejected 57 times, almost always by
   misreading the rules: it treated the 10-minute ECG deadline as a 10-minute onset window, got
   ages and months wrong, called alert severity the scenario level, and once rejected
   `CHEST_PAIN_ECG_10MIN` "instead of the defined `CHEST_PAIN_ECG_10MIN`". Codex rejected 3, all
   fair: "unclear speech" does not establish the gold complaint "slurred" (twice), and a
   right-arm laterality has no gold fact. Those answers were rewritten (a heavy tongue, one arm)
   and approved. The back-translation stage rejected 6 times (and the CLI path 4 times while a
   rewritten draft had no local check yet). Codex used 13 CLI calls, a format probe included.

The Arabic-only fields (`dialect`, `gloss_en`, `states`) are left out of an English scenario's
content hash, so every English review, the dataset hash and the frozen English baseline stay valid.

### What the back-translation check found

The pre-check (authoring QA, before the formal review) flagged 120 of 476 answers in 72 of 100
scenarios; 92 were misreadings by the 9B model, 28 only chrF. The misreadings are findings about
the model, which also extracts:

- Levantine لأ ("no") was translated "Yes" (or "For") every time it stood alone.
- صرله / صرلها / صرلي ("it's been …") became "diarrhea", "cough", "seizure", "took it".
- تنين ("two") became "Snake" or "Nine"; وايد (Gulf "very") became "waited" or "patients".
- Homographs: سن (tooth / age), نفسي (my breath / myself / my wish), حالي (myself / current).
- منيح / منيحة ("fine") became "weak", "fasting", "dizzy"; دينة (ear) became "religion".
- A leading "امم" once became "No".

Rewrites follow M2-01's rule (up to five per answer). Where the back-translation was right but
worded differently, the gloss was made more literal. Where the model misread a dialect word, the
Arabic was rewritten with another authentic form (لا for لأ, من يومين for صرله يومين, اتنين for
تنين, تنفسي for نفسي). Answers that passed keep their dialect, including some standalone لأ.

**Consequence.** The same model family back-translates and extracts, so the gate removed wording
the model misreads. The approved Arabic set is biased toward what Qwen3.5-9B understands, and its
extraction scores are optimistic for real Gulf and Levantine speech. The report's caveats say so.

### The harness

- `extraction_ar` shares `extraction_en`'s code (`language` selects scenarios and prompt).
  English rows, metrics and replay are unchanged; CI still replays the frozen English baseline.
- Arabic free-text facts (complaint, times, places, allergies, symptoms) are scored by presence
  only (`field-values-v1+ar-free-text-presence-v1`), because the extractor writes English
  renderings whose wording is not extraction quality. Coded and numeric values are exact.
- Each row records span counts: facts kept, PATIENT_STATED facts whose span slices to the quote,
  and dropped facts. `span_verified_rate` = kept / (kept + dropped).
- `careflow-eval run extraction --lang en|ar|all` writes each suite's report (replays under
  `replay-verification/`) and `eval/reports/extraction.json` and `.md`: missed expected alerts
  first (or "none"), incomplete extractions, per language and per dialect, per field, spans,
  models (Jais 2: skipped, see below), caveats, `publish: false`, and the agent's review of 10
  random scenarios, labelled "agent review, not a native speaker". Scores are decimal fractions,
  never percentages.

### What the live Arabic run found, and one fix

The recorded run (`eval/reports/extraction_ar/20260928-081617.json`) completed all 100 cases,
with one missed expected alert, `ar-gul-child-01`'s `NG143_RED_FLAG`. The parent's
"مدري، مو متأكدة" ("I don't know, I'm not sure") to "is your child hard to wake?" was extracted as
`no`. It is one of 10 of the 15 `unknown` gold facts the model returned as `no`: in Arabic it reads
"not sure" as "no". In the intake graph, the deterministic explicit-answer reader (ADR-052) reads
9 of those 10 replies as `unknown`, and `with_explicit` keeps an unsure reading over the model's
no, so the red flag is raised there. The tenth, "والله ما أدري، ما انتبهت" ("honestly, I don't
know, I didn't notice"), began with a filler, so the reader returned nothing and the model's no
stood. `tap.explicit_answer` now skips leading fillers (والله، يعني، امم، طيب، بصراحة; "um",
"well", "honestly") before the unsure check only. Yes and no are read exactly as before, so the
change can only turn an answer into `unknown`.

The extractor also never returns `pain_present` or `symptoms`, in Arabic as in the frozen English
baseline (0 of 36 and 0 of 20 here; 0 of 13 each in English). This is a prompt matter for the
extractor in both languages, left as a finding.

### Jais 2 (optional comparison): skipped

No ungated, pre-converted mlx-community 4-bit Jais 2 build exists (Hugging Face, 28 September).
The official `inceptionai/Jais-2-8B-Chat` and its `inception42` mirror are gated (accepting their
terms is an owner decision), mlx-lm 0.31.3 has no `Jais2ForCausalLM`, and converting the bf16
weights needs about 16 GB RAM. The report's `models` block records this.

### Tests

- `eval/tests/test_arabic.py`: the seeded plan and its coverage, drafts from answers, the Arabic
  validators and gold support, the back-translation stage (a flagged answer never reaches the
  judge; matching answers are recorded with their rows; a CLI judge reuses a recorded pass and
  rejects a draft without one), presence scoring, and the per-language report built from the
  English replays.
- `eval/tests/test_codex_review_provider.py`: the Codex adapter at the subprocess boundary (the
  isolated argv and environment, identity from a fake session log, fail-closed on another model,
  effort or provider, an API-key login, a failed turn, the live runtime and the budget).
- `eval/tests/test_arabic_dataset.py`: at least 90 approved scenarios from both dialects, every
  rule id expected at least 3 times, every approved answer's recorded back-translation passing.
- `eval/tests/test_arabic_replay.py`: the recorded Arabic run replays exactly, 100% of its
  PATIENT_STATED facts slice to their quote, and the committed `extraction.json` is the one the
  replays produce.
- `services/intake/tests/test_extraction_arabic.py` (`-k arabic`): the Arabic prompt, spans into
  the original text (diacritics, tatweel, hamza, a decomposed madda), numerals, the ten negation
  cases and unsure versus no on the recorded local model, dropped facts, model failure, and 100%
  of PATIENT_STATED facts slicing to their quote.

## Consequences

- The review evidence (log, receipts, back-translation rows) is in `eval/datasets/`.
- Known limit, inherited from M1-18: the model receipts bind the exact served id, which is the
  local snapshot path, so they contain the local cache path (as the English ones do).
- The Arabic results are technical and unpublished. No Arabic accuracy percentage appears in the
  UI, the README or any public material.
