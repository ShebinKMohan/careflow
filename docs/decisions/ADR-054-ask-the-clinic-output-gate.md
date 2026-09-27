# ADR-054: An output gate on every generated answer, and the measured "I don't know" threshold

Status: accepted, 27 September 2026 (SHE-306 [M2-09], the test phase of ADR-040)

## Context

ADR-040 built the phone-only "ask the clinic" agent: a two-layer medical classifier that fails
safe, three tools, cited answers, and a refusal with a nurse flag. Its tests were left pending.
The review found three more problems:

- **A valid citation does not make an answer safe.** `check_citations` checked that each cited id
  was a source. A model could still cite the pharmacy section and add a dose, and that text would
  reach the patient.
- **Arabic phrases with the definite article were missed.** The lexicon matched an Arabic phrase
  only as whole words, so "المضادات الحيوية" (the antibiotics) slipped past the phrase
  "مضادات حيوية".
- **The "I don't know" threshold was a guess.** `min_score: 0.45` came from a 22-question scratch
  check. SHE-304's eval measured it: it refused 9 of 60 answerable questions.

## Decision

### The output gate (`careflow_rag.output_gate`)

Every generated answer goes through a new graph node after `check_citations`. The node is plain
rules, independent of the model that wrote the answer. It blocks, in English and Arabic:
- **a dose** or amount of medicine;
- **a diagnosis** or a statement about what the patient has;
- **reassurance or triage advice**: "don't worry", "nothing serious", "go home", "it can wait",
  "keep taking your antibiotics";
- **an ungrounded medical term**: an entry of the question lexicon that is in the answer but not
  in the text of the sources the answer cites.

How the grounding works:
- The handbook is practical and reviewed (ADR-053), so a medical word the model adds on its own
  did not come from the sources. "Bring a list of the medicines you take" is grounded;
  "paracetamol" never is.
- The lexicon's lines are split into their alternatives (`Lexicon.entries`), so "medicines" in a
  source does not ground "pills" in an answer.
- The Arabic patterns are written in the lexicon's normal form, ى as ي: "على" is matched as
  "علي".

A blocked answer is never shown. The graph routes it to `refuse_and_flag`: the patient gets the
approved refusal, and the nurse gets one flag with the new category `output_gate`, as for any
medical question (safety rule 2: when unsure, refuse and flag).
- The category is added to the `rag.refused` event contract and to the database constraint
  (migration 0012).
- The log names the rules that fired, never the answer or the question.

### Arabic phrases

A lexicon phrase matches a run of consecutive words, each word through the same prefix and suffix
removal as single words. All 20 medical test questions are now caught by the lexicon alone, with
no model call.

### The threshold

`min_score: 0.30`, from SHE-304's distribution:
- the 60 answerable questions scored 0.351 to 0.848;
- the 10 out-of-scope questions scored 0.175 to 0.347.

At 0.30 every answerable question is answered, with a margin of 0.051. One Arabic out-of-scope
question (0.347) reaches the answer model, which is still bound by the sources, the citation check
and the gate.

0.35 would split the two groups, but by only 0.001 to 0.003, too fragile for a different model
revision or wording. Neither error is a safety failure: medical questions never depend on this
threshold. A test checks the configured value against the report.

## Consequences

- **The handoff's "valid-citation unsafe answer" is closed.** Tests cover it with a scripted model
  and a poisoned chunk, and on the real route.
- **The gate has no false alarm on the handbook:** each of its 50 sections, quoted as the answer
  citing it, passes. In the recorded run, the real model's 12 practical answers (6 English, 6
  Arabic) pass too.
- **A regex gate is a backstop, not a guarantee.** The classifier, the prompt and the citations
  come first, and the gate's lists grow as medical words the model uses are found (SHE-310 is the
  larger eval).
- **Flags are kept on a first miss, which re-queues the patient** (the grace rule), and deleted on
  the no-show or the close.
