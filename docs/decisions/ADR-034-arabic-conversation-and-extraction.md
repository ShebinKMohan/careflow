# ADR-034: Arabic conversation and extraction

## Status

Accepted, 27 Sep 2026 ([M2-03]). This is the core build. The Arabic scenarios, the per-language harness and report, the recorded Arabic fixtures and the unit tests follow in the test phase.

## Context

The rules engine and the urgency model read only coded facts. An Arabic-speaking patient must therefore produce the same English-coded `StructuredFacts` as an English speaker, each pointing to a verified span of their own words (safety rules 1 and 4). Since [M2-01] the session carries `language: en | ar`, but the conversation, the extractor and the verifier were English-only.

## Decision

### The session language picks everything

`IntakeState.language`, set at check-in, chooses four things:

- the approved templates (`templates_en.yaml` or `templates_ar.yaml`);
- the phrasing prompt;
- the extraction prompt;
- `ExtractionResult.prompt_version`.

`Extractor.extract` takes `language` (default `en`), and the extract node passes the session's. English behaviour and the English prompts are unchanged, so the English cassettes still replay.

### Conversation

- **Templates.** `templates_ar.yaml` mirrors the English file key for key.
  - The questions are short Modern Standard Arabic.
  - Where possible the wording avoids gendered verb forms, because the patient's gender is not known.
  - Numbers use Western digits (ADR-032).
  - An allergy list is joined with `،` and `و`.
- **Phrasing.** The LLM may still only rephrase an approved template. The Arabic prompt asks for one short Modern Standard Arabic question ending with `؟`. A reply is accepted only if all of the following hold:
  - it contains exactly one question mark (`?` or `؟`);
  - it ends with `؟`;
  - it has at most 40 words;
  - it passes the output filter.
  
  Anything else falls back to the template.
- **Output filter.** The same checks now also run on the Arabic-normalised text:
  - diagnosis wording and hedges;
  - condition names;
  - advice;
  - doses, including Arabic-Indic digits;
  - more than one question.
  
  Every Arabic template, and the nurse-coming message, passes it. The regex stays a backstop, not a guarantee (safety rule 2).
- **Nurse-facing text.** The staff popover shows an Arabic quote in `<q dir="auto">`, so it reads right to left inside the English UI.

### Extraction

- **Prompt.** `extraction_v2_ar` mirrors the current English `extraction_v2`, rule for rule. The issue's `extraction_v1_ar` name predates the English prompt's v2.
  - Keys, yes/no values and symptom names stay English codes.
  - Free-text values (chief complaint, onset, allergies, …) are short, faithful English renderings. Only `quote` is Arabic, copied verbatim, so the nurse and doctor read English and can open the patient's own words.
  - The prompt lists Gulf and Levantine negators (ما، مو، مش، مب، ما في، لا، ما عندي، ليس، لم), and says a denial is `false` with `negated: true`.
  - Its example covers MSA, Gulf and Levantine forms.
- **Yes/no words.** `_tri_state` also accepts Arabic yes, no and unsure words (نعم، إي، أيوه / لا / ما أدري، مدري, and so on), normalised, in case a model returns words instead of booleans. A "yes" that contradicts `negated` is still dropped.
- **Negation stays in the model.** There is no code-level Arabic negation flip. Flipping yes to no on a negator anywhere in the quote would turn "ما أدري، بس عندي ألم في صدري" ("I don't know, but I have chest pain") into a missed alert. The model's `negated` flag and the contradiction check stay the mechanism, and the scenario suite will measure it.

### Span and number verification

- **Normalisation** (`careflow_intake/arabic.py`) is applied one character at a time, before matching:
  - drop tatweel (U+0640) and diacritics (U+064B–U+0652, U+0670);
  - map أ, إ and آ to ا;
  - compose a letter with a following combining mark (NFC, for example ا + U+0654 → أ → ا);
  - casefold, as before.
- **Offsets.** `verify._normalise` keeps each normalised character's start and end in the original. A dropped mark belongs to the letter before it, so a verified span covers the patient's original characters, marks included. `VerifiedFact.quote` is always the original slice.
- **Numbers.**
  - Arabic-Indic (٠–٩) and Eastern Arabic-Indic (۰–۹) digits count as digits.
  - Arabic number words 0–10 count too, in MSA and dialect spellings (for example ثنين، تلاتة، تمانية), and with a final ة written ه.
  - A word matches only as a whole token, optionally after و. That keeps "لست" ("I am not") from verifying a six.

## Consequences

- With the default configuration, an Arabic session gets Arabic questions, Arabic phrasing (when enabled) and English-coded facts with verified Arabic spans. The rules engine and the urgency model run on the same codes for both languages.
- The Arabic wording has not been reviewed by native speakers. The footer says so (ADR-032), and no Arabic accuracy figure is published anywhere.
- Pending for the test phase:
  - the `-k arabic` unit tests: prompt selection, original offsets and the 10 negation cases;
  - `eval/scenarios/generate.py --lang ar` and the judge, for at least 90 approved scenarios covering every rule at least 3 times;
  - the harness with `--lang all`, writing `eval/reports/extraction.json` and `.md` with missed alerts first;
  - the recorded Arabic fixtures under `tests/fixtures/extraction/ar/`;
  - the optional Jais comparison ("skipped: RAM" unless an ungated mlx build exists).
