# Intake English dataset review record

Current status: **50 independently and automatically reviewed synthetic scenarios**.
The default loader validates all 50, with dataset SHA
`b8e3c26807e65a4d634c29bbb4fa783e78f5880eb22399c1bcd019e49d4f0bc9`.
The writer is GPT-6; reviewers are local Qwen (29 cases) and offline Claude Sonnet 5
(21 cases). Actual raw verdicts, model identity evidence and content hashes are retained
under `eval/datasets/review-receipts/`, with decisions in `eval/datasets/review-log.jsonl`.
The authoring history and failed-review audit below are retained as historical records.
This is automated dataset review, not clinician review or clinical validation.

## Draft authoring: 27 September 2026

Status: **50 synthetic drafts; independent review pending.** The files are in
`eval/datasets/intake_en/_inbox/`. No file has review metadata, and none is approved for the
reviewed evaluation dataset. This document records authoring provenance and review work still
needed; it does not record a completed model or clinical review.

| Category | Drafts | Deliberate coverage |
| --- | ---: | --- |
| `chest_pain` | 8 | Current pain, radiation, associated signs, denials and uncertainty |
| `stroke` | 6 | Individual FAST signs, multiple signs, unknown last-known-well and uncertainty |
| `sepsis` | 8 | Partial NEWS2 at 4/5/6, measured fever despite denial, low temperature, rigors, nurse completion |
| `child` | 6 | Under-five red/amber features, parent uncertainty, combined alerts and older-child waking rule |
| `breathing` | 4 | Isolated breathlessness without a matching rule, including abnormal oxygen saturation |
| `routine` | 18 | Focused musculoskeletal, skin, ear, gastrointestinal and minor surface complaints |
| **Total** | **50** | English only; 4–6 patient turns per draft |

Writer: `coding-agent` / `GPT-6`, as recorded in each file's `generated_by`. The 18 `routine-*`
files were authored by a delegated agent using the same model identity. That delegation is
authoring, not an independent review. A reviewer using a different model must assess the files
before any review metadata is added or files are promoted.

Every draft explicitly declares `synthetic: true`. All identities are fictional and unnamed.
There are no patient contact details or identifiers.
Age and sex are scenario context in `patient.age_years` and `patient.sex`. They are not
patient-extraction gold because these scripts do not ask the patient to state them. Vital signs
are explicit synthetic measurements in `device_vitals` and, in two sepsis drafts,
`nurse_vitals`. They must never be passed to the extractor as expected answers or counted as
patient-stated facts. No vital sign appears in `expected.facts`.

Every expected extraction fact is supported by an existing patient turn. Gold can include a
fact stated in an answer to another question: matching `question_key` is not required for
cross-field statements. No extra turns were added to manufacture that support. Free-text gold
uses the answer or an exact supported phrase; numeric gold is explicitly spoken as a digit or
number word. The scripts use approved English question keys. The harness supplies the
corresponding approved question text.

### Gold-completeness authoring audit before any baseline

The writer reread `services/intake/src/careflow_intake/extraction/prompts/extraction_v2.md` and
audited the drafts before any model baseline. Rule 3 requires extracting everything the patient
states; rule 4 directs facts with dedicated keys to those keys rather than `symptoms`; rule 9
forbids invented diagnoses or unstated causes. The earlier addressed-question-only scope was
therefore removed. Supplementary gold now includes supported pain presence, pain location and
non-keyed symptom phrases.

Dedicated chest-pain, FAST, infection, child and breathlessness keys are not duplicated as
`symptoms`. Vague answers such as "I feel sick" are not converted to a diagnosis or a specific
symptom such as nausea. A denial of chest pain or fever is not a denial of all pain. The
`chest-pain-unsure-radiation` script states tightness but never explicitly reports pain or a pain
score, so general pain presence/location is not inferred from it. These choices require
independent review against the prompt; they are not tuned to model outputs.

The non-routine supplementary gold is recorded below. `pain_location: chest` is an exact word
from the cited patient's chest statement, not an anatomical synonym.

| Draft ID | Added gold | Existing patient support |
| --- | --- | --- |
| `chest-pain-radiating-arm` | `pain_present: yes`; `pain_location: chest` | "Yes, I have chest pain now." |
| `chest-pain-sweating` | `pain_present: yes`; `pain_location: chest` | "Seven out of ten." answers the pain question; "I have discomfort in my chest." locates it |
| `chest-pain-localised` | `pain_present: yes`; `pain_location: chest` | "I have pain in my chest." |
| `chest-pain-no-breathlessness` | `pain_present: yes`; `pain_location: chest` | "My chest hurts." |
| `chest-pain-with-jaw-radiation` | `pain_present: yes`; `pain_location: chest` | "There is pain in my chest." |
| `chest-pain-screen-negative` | `pain_present: no` | "Zero out of ten." answers the pain question |

The routine supplementary gold uses the exact existing patient phrases below. Every positive
pain-presence addition is supported by a nonzero answer to the approved pain-score question.
Those seven cases already contain `pain_location` from a dedicated patient answer, so no new
location was inferred from an itchy, stiff or otherwise uncomfortable body site.

| Draft ID | Added gold | Existing patient support |
| --- | --- | --- |
| `routine-01-wrist-ache` | `pain_present: yes` | "2" answers `pain_score`; also "Left wrist ache" |
| `routine-02-ankle-soreness` | `pain_present: yes` | "2" answers `pain_score` |
| `routine-03-knee-ache` | `pain_present: yes` | "3" answers `pain_score`; also "Left knee ache" |
| `routine-04-shoulder-stiffness` | `symptoms: ["Right shoulder stiffness"]` | "Right shoulder stiffness" |
| `routine-05-elbow-tenderness` | `pain_present: yes`; `symptoms: ["Left elbow tenderness"]` | "2" answers `pain_score`; "Left elbow tenderness" |
| `routine-06-heel-discomfort` | `pain_present: yes` | "1" answers `pain_score` |
| `routine-07-finger-ache` | `pain_present: yes` | "2" answers `pain_score`; also "Right index finger ache" |
| `routine-08-dry-skin` | `symptoms: ["Dry skin"]` | "Dry skin" |
| `routine-09-scalp-itch` | `symptoms: ["Itchy scalp"]` | "Itchy scalp" |
| `routine-10-flaky-skin` | `symptoms: ["Flaky skin patch"]` | "Flaky skin patch" |
| `routine-11-ear-itch` | `symptoms: ["Left ear itch"]` | "Left ear itch" |
| `routine-12-blocked-ear` | `symptoms: ["Blocked right ear"]` | "Blocked right ear" |
| `routine-13-bloating` | `symptoms: ["Bloating"]` | "Bloating" |
| `routine-14-constipation` | `symptoms: ["Constipation"]` | "Constipation" |
| `routine-15-queasiness` | `symptoms: ["Queasiness"]` | "Queasiness" |
| `routine-16-toenail-irritation` | `symptoms: ["Left big toenail irritation"]` | "Left big toenail irritation" |
| `routine-17-foot-blister` | `pain_present: yes`; `symptoms: ["Blister on my left foot"]` | "1" answers `pain_score`; "Blister on my left foot" |
| `routine-18-dry-lips` | `symptoms: ["Dry lips"]` | "Dry lips" |

The authoring audit added 31 facts in total: 13 pain-presence facts (12 yes and 1 no), 5
chest-location facts and 13 symptom entries. Pure aches, soreness and discomfort use the
dedicated pain facts; explicit tenderness retains its symptom quality as well. These choices
remain draft annotations for independent assessment. No model output was used to select the
wording or gold values.

### Rule expectations and limits

Expected alert sets were authored against the implemented rules source, not inferred from a
clinical diagnosis. Programmatic consistency checks have not yet been run as part of this
authoring task. The independent review must check every alert set after constructing facts
with the correct context and measured provenance.

- Current chest pain `yes` activates `CHEST_PAIN_ECG_10MIN`. Negative or unknown current-pain
  answers do not; associated signs alone do not activate that rule.
- At least one FAST answer `yes` activates one `FAST_STROKE` alert. Multiple positive answers
  must not duplicate it. Unknown last-known-well does not suppress a positive screen.
- `SEPSIS_NEWS2` needs infection evidence plus NEWS2 of at least 5. The set includes a partial
  score of 4 that must stay below that rule threshold, an exact score of 5 and two cases whose
  nurse measurements complete the score. Unmeasured parameters remain missing.
- Under-five parent red answers include an unknown waking answer; combined red and amber
  features retain both alerts. The eight-year-old waking case expects `CHILD_HARD_TO_WAKE`,
  not an under-five or adult rule.
- The breathing cases intentionally expect no rule alert where the current rule set has no
  matching trigger. In particular, `breathing-low-oxygen-no-infection` exposes an adult
  respiratory coverage gap. Its empty alert set does not imply that the scenario is safe or
  that no nurse escalation is needed.

`expected.level` values and rationales are provisional synthetic evaluation labels. They are
not clinician-reviewed, validated KTAS labels, model predictions or clinical instructions.
The routine scenarios have no measured vitals; an empty measurement map must not be converted
to normal readings. No draft may be used to claim clinical accuracy or product readiness.

### Review and execution record

Pending work: schema and semantic validation, the exact category-count gate, PHI denylist
checks, gold-support and field-scope review, deterministic rule consistency, a different-model
review and a content-bound review record. Record actual findings and corrections below when
those steps have run. A reviewed flag alone is not evidence that these checks took place.

No service, model, provider, evaluation CLI, runtime journey or live baseline was run during
this draft-authoring task. No result metrics or replay claims are made here.

| Date | Stage | Actor | Outcome | Evidence |
| --- | --- | --- | --- | --- |
| 2026-09-27 | Draft authoring | coding-agent / GPT-6 | 50 unreviewed drafts created in the inbox | Per-file `generated_by`; this record |
| 2026-09-27 | Gold-completeness authoring audit | coding-agent / GPT-6 | Added `synthetic: true` to all 50 drafts and 31 supported gold facts; independent review still pending | Exact support tables above; production `extraction_v2.md` rules 3, 4 and 9 |

## Clarification after actual Qwen review

The independent local Qwen run `20260927-091005-791808` accepted 24 cases and left 26 unapproved. Fifteen failures used content hashes in the verdict ID field; those drafts remain byte-for-byte unchanged. Eleven failures misread thresholds, age arithmetic, patient answers or the distinction between extraction facts and rule inputs. Their existing rule expectations were verified against the actual engine. Only those eleven `expected.level_rationale` strings were expanded with explicit existing inputs and computations. No patient turns, fact/alert gold, urgency levels, demographics, vitals, generation metadata or accepted scenarios changed.

The exact old/new text, original rejection reasons, immutable receipt paths and before/after content hashes are recorded in [M1-18-REVIEW-AUDIT-2026-09-27.json](M1-18-REVIEW-AUDIT-2026-09-27.json). The record preserves the failed decisions; it grants no approval. All 26 require a fresh independent review. All 50 still pass deterministic schema, source-support and rule consistency checks. The complete post-clarification dataset hash is `b8e3c26807e65a4d634c29bbb4fa783e78f5880eb22399c1bcd019e49d4f0bc9`.
