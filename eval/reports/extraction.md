# Extraction by language (M2-03)

Technical report, not for publication (`publish: false`). Synthetic scenarios only; the Arabic was not validated by native speakers. Scores are decimal fractions from one recorded local run, not clinical or translation accuracy.

## Missed expected alerts

| Scenario | Language | Dialect | Missed | Raised | Status | Error |
| --- | --- | --- | --- | --- | --- | --- |
| child-parent-unsure-waking | en | n/a | NG143_RED_FLAG | none | completed | timeout |
| ar-gul-child-01 | ar | gulf | NG143_RED_FLAG | none | completed | n/a |

## Incomplete extractions

- `child-parent-unsure-waking` (en): completed, timeout

## By language

| Language | Cases | Completed | Field precision | Field recall | Field F1 | Alert recall | False-alert rate | Span-verified rate | PATIENT_STATED slices |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| en | 50 | 50 | 0.803 | 0.690 | 0.742 | 0.960 | 0.000 | 0.988 | 1.000 (238 of 238) |
| ar | 100 | 100 | 0.979 | 0.852 | 0.911 | 0.978 | 0.000 | 0.998 | 1.000 (475 of 475) |
| ar_gulf | 50 | 50 | 0.975 | 0.850 | 0.908 | 0.957 | 0.000 | 1.000 | 1.000 (238 of 238) |
| ar_levantine | 50 | 50 | 0.983 | 0.853 | 0.914 | 1.000 | 0.000 | 0.996 | 1.000 (237 of 237) |

Arabic free-text facts are scored by presence and English ones by exact value, so the two languages' field scores are not comparable. Coded and numeric facts are exact in both.

## Per field: en

| Field | Scored by | TP | FP | FN | Precision | Recall | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `allergies_confirm` | exact value | 13 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `chest_pain.onset` | exact value | 0 | 4 | 4 | 0.000 | 0.000 | 0.000 |
| `chest_pain.present` | exact value | 12 | 1 | 1 | 0.923 | 0.923 | 0.923 |
| `chest_pain.radiates` | exact value | 5 | 1 | 2 | 0.833 | 0.714 | 0.769 |
| `chest_pain.short_of_breath` | exact value | 10 | 0 | 1 | 1.000 | 0.909 | 0.952 |
| `chest_pain.sweating` | exact value | 3 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `chief_complaint` | exact value | 18 | 31 | 32 | 0.367 | 0.360 | 0.364 |
| `child_amber.poor_feeding` | exact value | 4 | 0 | 1 | 1.000 | 0.800 | 0.889 |
| `child_amber.reduced_urine_output` | exact value | 3 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `child_danger.grunting` | exact value | 4 | 0 | 1 | 1.000 | 0.800 | 0.889 |
| `child_danger.hard_to_wake` | exact value | 5 | 0 | 1 | 1.000 | 0.833 | 0.909 |
| `child_danger.non_blanching_rash` | exact value | 2 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `child_danger.pale_mottled_ashen_or_blue` | exact value | 0 | 0 | 1 | n/a | 0.000 | 0.000 |
| `fast.arm_weakness` | exact value | 6 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `fast.face_droop` | exact value | 6 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `fast.last_known_well` | exact value | 2 | 4 | 4 | 0.333 | 0.333 | 0.333 |
| `fast.speech_difficulty` | exact value | 6 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `infection.fever_reported` | exact value | 25 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `infection.rigors` | exact value | 10 | 0 | 1 | 1.000 | 0.909 | 0.952 |
| `pain_location` | exact value | 7 | 0 | 5 | 1.000 | 0.583 | 0.737 |
| `pain_present` | exact value | 0 | 0 | 13 | n/a | 0.000 | 0.000 |
| `pain_score` | exact value | 13 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `severity` | exact value | 19 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `symptom_onset_at` | exact value | 18 | 6 | 6 | 0.750 | 0.750 | 0.750 |
| `symptoms` | exact value | 0 | 0 | 13 | n/a | 0.000 | 0.000 |

## Per field: ar

| Field | Scored by | TP | FP | FN | Precision | Recall | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `allergies_confirm` | presence | 36 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `chest_pain.onset` | presence | 14 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `chest_pain.present` | exact value | 24 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `chest_pain.radiates` | exact value | 10 | 4 | 4 | 0.714 | 0.714 | 0.714 |
| `chest_pain.short_of_breath` | exact value | 9 | 0 | 1 | 1.000 | 0.900 | 0.947 |
| `chest_pain.sweating` | exact value | 14 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `chief_complaint` | presence | 100 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `child_amber.poor_feeding` | exact value | 8 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `child_amber.reduced_urine_output` | exact value | 4 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `child_danger.grunting` | exact value | 8 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `child_danger.hard_to_wake` | exact value | 11 | 1 | 1 | 0.917 | 0.917 | 0.917 |
| `child_danger.non_blanching_rash` | exact value | 4 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `fast.arm_weakness` | exact value | 12 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `fast.face_droop` | exact value | 10 | 2 | 2 | 0.833 | 0.833 | 0.833 |
| `fast.last_known_well` | presence | 12 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `fast.speech_difficulty` | exact value | 12 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `infection.fever_reported` | exact value | 28 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `infection.rigors` | exact value | 13 | 3 | 3 | 0.812 | 0.812 | 0.812 |
| `pain_location` | presence | 22 | 0 | 14 | 1.000 | 0.611 | 0.759 |
| `pain_present` | exact value | 0 | 0 | 36 | n/a | 0.000 | 0.000 |
| `pain_score` | exact value | 36 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `severity` | exact value | 22 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `symptom_onset_at` | presence | 56 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `symptoms` | presence | 0 | 0 | 20 | n/a | 0.000 | 0.000 |

## Models

- qwen3.5-9b-4bit (en): run. mlx-community/Qwen3.5-9B-MLX-4bit@938d8919941c6e7efd3c7150eff7fe9d12afa631, prompt extraction_v2, replay
- qwen3.5-9b-4bit (ar): run. mlx-community/Qwen3.5-9B-MLX-4bit@938d8919941c6e7efd3c7150eff7fe9d12afa631, prompt extraction_v2_ar, replay
- Jais 2 8B Chat (ar): skipped. No ungated pre-converted mlx-community 4-bit Jais 2 build exists (Hugging Face search, 28 Sep 2026). The official inceptionai/Jais-2-8B-Chat (and its inception42 mirror) is gated: accepting its terms is an owner decision. mlx-lm 0.31.3 has no Jais2ForCausalLM architecture, and converting the bf16 weights needs about 16 GB RAM (skipped: RAM).

## Agent review of 10 random Arabic scenarios

*Agent review, not a native speaker.* Sampled with seed 290.

- `ar-gul-breathing-01`: Reads as natural Gulf speech (ما يعورني، وايد، زين). Each answer states one fact; a breathless patient would more likely add when it started or what brings it on.
- `ar-gul-chest-pain-02`: مدري، مو متأكدة is a typical Gulf unsure reply. The pain score is a bare الألم ثمانية; real patients often hedge (يمكن ثمانية) or describe the pain instead of rating it.
- `ar-gul-child-03`: Plausible parent speech (الحفاظات مبلولة مثل العادة). The complaint says the one-year-old does not eat (ما ياكل) while the feeding answer says he breastfeeds (يرضع); a parent would more likely say ما يرضع from the start.
- `ar-gul-child-05`: An unsure answer about waking a 13-year-old is realistic, as is a week-long change in behaviour. No hesitation or detail about what 'not herself' means, which a worried parent would usually give.
- `ar-gul-routine-14`: Natural and consistent: a mild headache rated one and called خفيف. Very clean.
- `ar-gul-sepsis-08`: Realistic spelling variation (انا without hamza) and the most natural answer in the sample (أرجف وما أقدر أوقف). The vitals are in the low NEWS2 band, so no alert despite fever and rigors, which matches the rules.
- `ar-lev-child-01`: Strong Levantine markers (كتير، فيقها، عم، منيح، لأ). It keeps the standalone لأ that the back-translation check rejected in most other scenarios, so the set has only a few answers of this kind.
- `ar-lev-routine-02`: Natural Levantine (بيوجعني، بلّش). The location answer repeats the complaint word for word, which is realistic for a short kiosk exchange.
- `ar-lev-routine-06`: One speaker spells back two ways: ضهري (Levantine) in the complaint and ظهري (standard) in the location answer. The second came from a back-translation rewrite, so this variation is an artefact, not an observation of real speech.
- `ar-lev-routine-08`: Natural (عم تحكني). The severity is a bare number with no description.

The dialogue is cleaner than real speech: one fact per answer, few hesitations, no self-corrections, no English code-switching (common in Gulf clinics) and no Gulf and Levantine mixing within a scenario. The back-translation check, run by the same 9B model family that extracts, pushed some answers toward standard forms (لا for لأ, ظهري, تنفسي) and away from words the model misreads, so the hardest dialect forms are under-represented and the Arabic scores are likely optimistic. Arabic fluency judgements here are the agent's own; no native speaker reviewed these scenarios.

## Caveats

- Synthetic scenarios only. The gold facts were sampled in code; the patient answers were written by a coding agent, and each scenario was reviewed by an automated model of another family, not by clinicians.
- The Arabic was not validated by native speakers. Each Arabic answer and its English gloss were checked only by machine: a blind back-translation, a model judgement and chrF >= 40 (the M2-01 check), then a local 9B model's review of the whole scenario.
- Arabic free-text facts (complaint, times, places, allergies, symptoms) are scored by presence only, because the extractor writes them as English renderings; yes/no/unknown and numeric facts are scored exactly, as in English. English free text is scored by exact value, so the English and Arabic field scores are not comparable.
- The eval measures the extractor alone, on the whole transcript. In the intake graph an explicit yes, no or not-sure reply to the question on screen is read first without a model (ADR-052), and an unsure reading beats a model's no, so a missed alert listed here from an unsure reply is kept as unknown there.
- These are technical measurements of one recorded local run (Qwen3.5-9B 4-bit), not clinical accuracy. Not for publication (publish: false). No Arabic accuracy percentage may appear in the UI, the README or any public material.
