# ADR-052: A parent's danger answer reaches the rules without a model

Status: accepted, 27 September 2026 (SHE-293 [M2-04], the verification half of ADR-035)

## Context

ADR-035 built the child flow and left its verification pending. That verification found three
problems.

- **A typed or spoken answer always needed the model.** ADR-035 read a tapped Yes / No / Not sure
  without the extractor, but a typed or spoken one still went to it.
  - SHE-314's English local baseline (`eval/replays/extraction_en`, case
    `child-parent-unsure-waking`, a recorded timeout) shows the failure: "I am not sure." to "Is
    your child very hard to wake…" made the local extractor time out.
  - The extraction was incomplete, so no fact was recorded, and `NG143_RED_FLAG` was missed.
  - In the graph, such a question was asked once more; after that the bank moved on, and a red
    question ended without an answer and without an alert.
- **An answer the model could not read left a red question silently unanswered.** This happened
  whenever the extractor failed or found nothing, even when the parent had answered.
- **A 15-year-old child got the adult model's suggestion.** The KTAS model's own age guard stops
  at 15 (its training data start there), but a child is under 16. So a 15-year-old checked in as
  "My child" got an urgency suggestion from a model trained on adults.

## Decision

### Explicit answers are read without a model (`careflow_intake/tap.py`)

A typed or spoken answer to a yes/no question is read before the extractor, in any input mode,
when it is explicit. The reading is asymmetric, because a missed danger answer is the worst
failure:

| The answer | Read as |
| --- | --- |
| Is, or begins with, an unsure phrase ("I am not sure.", "not sure, she's sleepy", "no idea", "maybe"; Arabic "لست متأكداً", "لا أعرف", "لا أدري", "ما أدري" …) | `unknown` |
| Begins with a yes word ("Yes, since this morning", "yeah", Arabic "نعم", "أيوه" …) | `yes` |
| Is nothing but a no ("No.", "nope", "No, she isn't.", Arabic "لا") | `no` |
| Anything else, e.g. "No, but she is very hard to wake", or "yes?" | not read: the extractor reads it |

- **Order.** Unsure phrases are checked first, so "لا أعرف" ("I don't know", which begins with
  "لا", no) and "no idea" are `unknown`, never `no`.
- **Question marks.** An answer with a question mark is only read as unsure: "yes?" may not be an
  answer.
- **The fact.** It is PATIENT_STATED and cites the whole answer turn, so the quote and the
  uncertainty are kept.
- **A turn that was only a question** (M2-10's router) is not read as an answer.
- **An answer that says nothing more** ("Not sure", "Yes.") needs no extractor at all.
- **An answer with more words** ("Yes, since this morning") still goes to the extractor, because
  the words may hold other facts. An adult's "Yes, and my left arm is weak" must still reach FAST.
  - The explicit reading of the question on screen is kept, whatever the extractor returns or if
    it fails.
  - One exception: the extractor's `yes` replaces an explicit `unknown`. Of two readings of one
    answer, the one that alerts sooner wins, since amber needs a yes to alert.
  - Such an answer waits for the extractor (bounded by `extraction_timeout_s`, 20 s). The alert
    for it is never lost, only delayed by at most that bound.

### A red question nobody could read becomes `unknown`, never `no`

This applies to a child's red question (the bank's 8 red questions) when the answer is not
explicit and the extractor failed or found nothing for it.

- **First unread answer.** Nothing is recorded, and the question is asked once more (as before;
  `MAX_ASKS` = 2).
- **Second unread answer.** The question is recorded as `unknown`, PATIENT_STATED, citing the
  parent's own second answer, and `extraction_incomplete` is set: the nurse's "check the answers
  with the patient".
- **What the rules do.** They raise `NG143_RED_FLAG` (under 5) or `CHILD_HARD_TO_WAKE` (5 to 15)
  with the fixed `parent_unsure` qualifier. That qualifier now also covers "the parent's answer
  could not be read". The nurse sees the parent's own words beside the alert.
- **Amber questions.** Nothing is recorded: amber needs a yes, so an invented `unknown` would
  change nothing and a `no` would hide the answer.

### A child gets no model suggestion

`suggest_urgency` returns no suggestion for `patient_type == "child"`, with the reason "a child: the
urgency model is trained on adults only", before the model is called. The nurse sets the level
alone, as for any missing suggestion (safety rule 3). An adult of 15 still gets the model's
suggestion (its guard allows 15).

### The nurse's alert lines are in the Arabic gate

`scripts/i18n/backtranslate.py` now also checks `packages/contracts/alert_templates.yaml`.
- **What it checks.** The Arabic `Alert.display_text` lines and their qualifiers, as the `safety.`
  keys `safety.alerts.<rule>` and `safety.qualifiers.<name>`.
- **Why.** They are the nurse's banner in an Arabic session, and SHE-273 did not check them.
- **Result.** All 8 pass. "Child amber flag" reads back as "Orange warning in the child", which the
  judge rated the same meaning (chrF 57.5).

### Evidence (SHE-293)

- **Scenarios.** `scripts/child_scenarios.py` writes the 101 scenario files: each of the 16 bank
  questions answered yes and "Not sure" at 2 months, 3 years and 9 years, plus 5 all-no controls.
  - The expected alerts come from the rules' stated semantics (ADR-035), not from running the
    engine.
  - `make lint` runs its `--check`, which fails when a file is missing, changed or stray, or when
    the bank changes without the files.
  - They are in `eval/scenarios/rules/child/`. The eval harness's `rules` suite (M1-18, SHE-314)
    replays a recorded baseline of the 56 files directly in `eval/scenarios/rules/`, and a
    recording is never rewritten (the harness refuses to). `services/rules` runs both folders.
    Adding the child set to the harness is a fresh recording for SHE-314.
- **Property test.** The hard-to-wake property test runs 500 examples.
- **Graph tests.** `services/intake/tests/test_child_answers.py`.
- **Journey.** `e2e/journeys/child-parent-path.spec.ts`, in English and Arabic.

## Consequences

- **SHE-314's failure cannot recur in the product.** "I am not sure." to a red question now raises
  the red alert without any model call. A model that is slow, down or unsure can no longer leave a
  child's red question unanswered.
- **SHE-314's baseline is kept.** It is an extractor-level replay: the eval suite calls the
  extractor directly (`eval/src/careflow_eval/suites/extraction_en.py`). It still records the
  extractor's timeout, which is true of the extractor.
- **No rule changed.** `RULESET_VERSION` stays 2026.3.0, the version ADR-035's build bumped for the
  child rules. The intake reads answers differently; the rules decide as before.
- **More `unknown` answers.** A red question may now show `unknown` where it showed nothing. The
  alert says "parent unsure", and `extraction_incomplete` sends the nurse to check.
- **The gap for ages 5 to 15 remains (ADR-035).** A 9-year-old's other red answers are recorded
  and shown, but only hard to wake alerts. The scenarios pin that behaviour, so a change to it is a
  deliberate rule change. Closing the gap is a clinical decision for the owner (SHE-327).
