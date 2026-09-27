# ADR-035: The child triage flow

## Status

Accepted, 27 Sep 2026 ([M2-04]). This is the core build. The scenario files, the Hypothesis property test, the `-k child` intake tests and the Playwright parent-path test follow in the test phase.

The test phase is done (SHE-293, docs/evidence/M2-04.md). It changed how answers are read, not the rules: ADR-052.

## Context

A parent checking in a feverish small child went through the adult steps, and three gaps made a missed NICE NG143 red flag possible:

- **Age.** The patient's age came only from the EHR record the kiosk found, and that record may be the parent's.
- **"Not sure".** A "Not sure" answer to a red question raised nothing, and was never asked again.
- **Tap answers.** They went through the LLM extractor like typed text. (The test stub even read "Not sure" as "no".)

For ages 5 to 15 no rule applied at all.

## Decision

### Kiosk: "Who is the patient?"

- **The parent path.** After check-in the kiosk asks "Who is the patient? Me / My child".
  - "Me" goes straight to the intake.
  - "My child" asks the age: large buttons for years (under 1 to 15), and for months when the child is under 2.
  - It then sends `POST /v1/sessions/{id}/patient` once.
- **After the questions.** The child path skips the vitals screens and ends on the done screen, which says "A nurse has been told. Please stay here." and "A nurse will check your child's temperature and breathing."
- **Messages.** The texts are `safety.child.*` in English and Arabic. The kiosk holds no thresholds.
- **Why a separate call.** The check-in has already started the graph by then. The new route is idempotent for the same answer and returns 409 for a different one. Its turns go into the transcript in the session language, so the age facts cite the parent's own answer (safety rule 4).

### The stated age wins

- **Facts.** A child's age becomes `patient.age_years` and `patient.age_months` (PATIENT_STATED). Because they are recorded later than the EHR age, they replace it.
- **Months.** `patient.age_months` is exact under 2. From 2 on it is the first month of the stated year. That lands in the same NG143 band as any month of that year, because the bands change at 3, 6, 12, 24 and 60 months.
- **The model never replaces it.** For a child session the extract node drops model-extracted age facts, so the LLM can never turn "1 year 4 months" into an age of 4 months.

### The child question bank

`conversation/child_questions.py` holds the NG143 features a parent can answer, each mapped to its existing contract fact. No new fact was needed.

- **Red:** hard to wake; no response; weak, high-pitched or continuous cry; pale, mottled, ashen or blue; grunting or struggling to breathe; non-blanching rash; stiff neck; seizure.
- **Amber:** less active; not smiling or responding as usual; poor feeding; fewer wet nappies; fever for 5 days or more; shivering fits (`infection.rigors`); a swollen joint or limb; not using a limb.

How the bank is asked:

- **When.** For `patient_type == "child"` the conversation asks the bank before any other question, except the first question already on screen.
- **Wording.** The approved `child_questions` templates are used verbatim: never rephrased by a model, and still passed through the output filter.
- **Repeats.** A question whose answer gave no fact is asked once more.
- **Caps.** The intake caps (8 answers or 120 s) never cut the bank short. Truncating it could drop a red question, so the caps apply only once the bank is done.

### Tap answers

- **The path.** The API now passes `input_mode` through to the graph. After a tap, the extract node maps the button label straight to the fact (`careflow_intake/tap.py`), with no model:
  - Yes, No and Not sure map to `yes`, `no` and `unknown`, in English and Arabic;
  - a number on the 0 to 10 scale maps to its whole number.
- **The fact.** It is PATIENT_STATED and cites the whole answer turn.
- **Fallback.** A label the mapper does not recognise goes to the extractor as before. Voice answers always do.

### Rules (ruleset 2026.3.0)

- **"Not sure" counts as red.** A parent's unknown on a red question they can answer counts as red for `NG143_RED_FLAG`. When only unsure answers raise the alert, it carries the fixed `parent_unsure` qualifier. Nurse-assessed red signs and amber answers still need a yes, so "Not sure" never goes unnoticed on a red question and amber noise does not grow.
- **`CHILD_HARD_TO_WAKE` (critical, new).** It raises for a child aged 5 to 15 whose hard-to-wake answer is yes or unknown (unknown adds `parent_unsure`). Its required action is `paediatric_review_now`.
- **Every age is covered.** Under 5, and when no age is known, `NG143_RED_FLAG` raises the same answer. So a drowsy child alerts at every age under 16, including "Not sure". The graph order is unchanged: `run_rules` still runs before `suggest_urgency`.

### The gap for ages 5 to 15

NG143 covers fever in children under 5. For a child aged 5 to 15, this ruleset has only `CHILD_HARD_TO_WAKE` and the rules that do not depend on age, such as FAST, chest pain and sepsis. NEWS2 does not apply under 16.

The other red answers are still recorded and shown to the nurse, but they raise no alert at those ages. A paediatric early-warning score (for example PEWS) would close the gap. It is out of scope and recorded here.

## Consequences

- A parent can check a child in, in either language. Red answers, "Not sure" included, reach the nurse as critical alerts through the rules, and no model can downgrade them (safety rules 1 and 3).
- Pending for the test phase:
  - the scenario files in `eval/scenarios/rules/`: every red and amber answer as yes and as unknown, at 2 months, 3 years and 9 years, plus 5 all-no controls;
  - the hard-to-wake property test (`max_examples>=500`);
  - `pytest services/intake -k child`;
  - `child-parent-path.spec.ts` in English and Arabic.
