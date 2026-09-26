# CareFlow to FHIR R4 mapping

Machine-readable source: [`mapping.yaml`](mapping.yaml). Integration code (M1-08 read client, M1-09 write-back, M1-16 briefing, M4-01 HL7, M4-05 CDS Hooks) implements exactly this table and nothing else. Research prototype. Synthetic data only. Not for clinical use.

**R4 only for now (FHIR 4.0.1); R5 and R6 go through the adapter later.** Per-hospital differences (Encounter class, identifier systems, profiles) live in the adapter config (`config/hospitals/*.yaml`), not in this table. No R5-only element is used.

## CareFlow code systems

CareFlow defines its own code systems under `https://careflow.example/fhir/CodeSystem/` where no standard code fits: `triage-level` (the level Observation and its `scale` component), `triage-scale` (KTAS, ESI, CTAS, MTS), `rule-id` (the six safety rules), `task-kind` (`ecg_within_10_min`), `provenance-source` (the five provenance tags), `tri-state` (yes, no, unknown answers), `complaint-category`, `symptom-severity` and `arrival-mode` (the KTAS arrival modes: walking, private_vehicle, public_ambulance, private_ambulance, other). Abbreviations below: `cf:` = CareFlow code system, `tho:` = `http://terminology.hl7.org/CodeSystem/`.

## Table

| Fact / output | Dir | Resource | Element | Code system | Code | Display | Unit | Verified |
|---|---|---|---|---|---|---|---|---|
| `patient.fhir_patient_id` | read | Patient | `Patient.id (matched on Patient.identifier)` | cf-sid:keypad-id |  |  |  | yes |
| `patient.age_years` | read | Patient | `Patient.birthDate` |  |  |  | a | yes |
| `patient.age_months` | read | Patient | `Patient.birthDate` |  |  |  | mo | yes |
| `patient.sex` | read | Patient | `Patient.gender` | http://hl7.org/fhir/administrative-gender |  |  |  | yes |
| `ehr_context.allergies` | read | AllergyIntolerance | `AllergyIntolerance.code` | tho:allergyintolerance-clinical | active | Active |  | yes |
| `ehr_context.conditions` | read | Condition | `Condition.code` | tho:condition-clinical | active | Active |  | yes |
| `ehr_context.medications` | read | MedicationStatement | `MedicationStatement.medicationCodeableConcept` |  | active |  |  | yes |
| `session.appointment` | read | Appointment | `Appointment.status` |  | booked |  |  | yes |
| `vitals.heart_rate` | write | Observation | `Observation.valueQuantity` | LOINC | 8867-4 | Heart rate | /min | yes |
| `vitals.respiratory_rate` | write | Observation | `Observation.valueQuantity` | LOINC | 9279-1 | Respiratory rate | /min | yes |
| `vitals.spo2` | write | Observation | `Observation.valueQuantity` | LOINC | 2708-6 | Oxygen saturation in Arterial blood | % | yes |
| `vitals.temperature_c` | write | Observation | `Observation.valueQuantity` | LOINC | 8310-5 | Body temperature | Cel | yes |
| `vitals.systolic_bp` | write | Observation | `Observation.component(8480-6).valueQuantity` | LOINC | 8480-6 | Systolic blood pressure | mm[Hg] | yes |
| `vitals.diastolic_bp` | write | Observation | `Observation.component(8462-4).valueQuantity` | LOINC | 8462-4 | Diastolic blood pressure | mm[Hg] | yes |
| `pain_score` | write | Observation | `Observation.valueQuantity` | LOINC | 72514-3 | Pain severity - 0-10 verbal numeric rating [Score] - Reported | {score} | yes |
| `vitals.acvpu` | write | Observation | `Observation.valueCodeableConcept` | SNOMED CT | 1104441000000107 | Alert Confusion Voice Pain Unresponsive scale score |  | yes |
| `vitals.on_supplemental_o2` | write | Observation | `Observation.code` | SNOMED CT | 371825009 / 722742002 | Patient on oxygen / Breathing room air |  | **no** |
| `rules.news2_total` | write | Observation | `Observation.valueQuantity` | SNOMED CT | 1104051000000101 | Royal College of Physicians NEWS2 (National Early Warning Score 2) total score | {score} | yes |
| `nurse_decision.final_level` | write | Observation | `Observation.valueInteger` | cf:triage-level | triage-level | Triage urgency level (1 = most urgent) |  | yes |
| `nurse_decision.scale` | write | Observation | `Observation.component(scale).valueCodeableConcept` | cf:triage-scale | KTAS / ESI / CTAS / MTS |  |  | yes |
| `nurse_decision.priority` | write | Encounter | `Encounter.priority` | tho:v3-ActPriority | S / EM / UR / R | stat / emergency / urgent / routine |  | yes |
| `session.encounter` | write | Encounter | `Encounter.status / Encounter.class` | tho:v3-ActCode | AMB | ambulatory |  | yes |
| `complaint_category` | write | Encounter | `Encounter.reasonCode` | cf:complaint-category |  |  |  | yes |
| `alert` | write | Flag | `Flag.code` | cf:rule-id | FAST_STROKE / CHEST_PAIN_ECG_10MIN / SEPSIS_NEWS2 / NG143_RED_FLAG / NG143_AMBER_FLAG / CHILD_HARD_TO_WAKE |  |  | yes |
| `alert.required_action` | write | Task | `Task.code / Task.restriction.period.end` | cf:task-kind | ecg_within_10_min | ECG within 10 minutes of arrival |  | yes |
| `alert.notification` | write | Communication | `Communication.category` | tho:communication-category | notification | Notification |  | yes |
| `briefing` | write | Composition | `Composition.type` | LOINC | 54094-8 | Emergency Department Triage Note |  | yes |
| `intake.questionnaire_response` | write | QuestionnaireResponse | `QuestionnaireResponse.questionnaire` |  | https://careflow.example/fhir/Questionnaire/careflow-intake |  |  | yes |
| `write_batch.provenance` | write | Provenance | `Provenance.agent.type` | tho:provenance-participant-type | performer / assembler / author / informant |  |  | yes |
| `write_batch.meta_tag` | write | (every written resource) | `meta.tag` | cf:provenance-source | EHR / PATIENT_STATED / DEVICE_MEASURED / NURSE_CONFIRMED / MODEL_INFERRED |  |  | yes |
| `ehr_context.allergies` | write | AllergyIntolerance | `AllergyIntolerance.verificationStatus` | tho:allergyintolerance-verification | unconfirmed | Unconfirmed |  | yes |
| `chief_complaint` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=chief_complaint).answer.valueString` |  |  |  |  | yes |
| `symptoms` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=symptoms).answer.item (group: name, onset, severity)` |  |  |  |  | yes |
| `fast.face_droop` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=fast.face_droop).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `fast.arm_weakness` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=fast.arm_weakness).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `fast.speech_difficulty` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=fast.speech_difficulty).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `fast.last_known_well` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=fast.last_known_well).answer.valueString` |  |  |  |  | yes |
| `chest_pain.present` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=chest_pain.present).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `chest_pain.radiates` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=chest_pain.radiates).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `chest_pain.sweating` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=chest_pain.sweating).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `chest_pain.short_of_breath` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=chest_pain.short_of_breath).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `chest_pain.onset` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=chest_pain.onset).answer.valueString` |  |  |  |  | yes |
| `infection.suspected` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=infection.suspected).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `infection.fever_reported` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=infection.fever_reported).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `infection.rigors` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=infection.rigors).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `child_danger.hard_to_wake` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=child_danger.hard_to_wake).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `child_danger.no_response_to_social_cues` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=child_danger.no_response_to_social_cues).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `child_danger.weak_high_pitched_or_continuous_cry` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=child_danger.weak_high_pitched_or_continuous_cry).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `child_danger.pale_mottled_ashen_or_blue` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=child_danger.pale_mottled_ashen_or_blue).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `child_danger.grunting` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=child_danger.grunting).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `child_danger.non_blanching_rash` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=child_danger.non_blanching_rash).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `child_danger.neck_stiffness` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=child_danger.neck_stiffness).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `child_danger.seizure` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=child_danger.seizure).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `child_danger.appears_ill_to_hcp` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=child_danger.appears_ill_to_hcp).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `child_danger.chest_indrawing` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=child_danger.chest_indrawing).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `child_danger.reduced_skin_turgor` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=child_danger.reduced_skin_turgor).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `child_danger.bulging_fontanelle` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=child_danger.bulging_fontanelle).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `child_danger.focal_neuro_signs` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=child_danger.focal_neuro_signs).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `child_amber.pallor_reported` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=child_amber.pallor_reported).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `child_amber.not_responding_normally` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=child_amber.not_responding_normally).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `child_amber.decreased_activity` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=child_amber.decreased_activity).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `child_amber.wakes_only_with_prolonged_stimulation` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=child_amber.wakes_only_with_prolonged_stimulation).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `child_amber.nasal_flaring` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=child_amber.nasal_flaring).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `child_amber.crackles` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=child_amber.crackles).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `child_amber.cap_refill_3s_or_more` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=child_amber.cap_refill_3s_or_more).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `child_amber.dry_mucous_membranes` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=child_amber.dry_mucous_membranes).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `child_amber.poor_feeding` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=child_amber.poor_feeding).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `child_amber.reduced_urine_output` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=child_amber.reduced_urine_output).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `child_amber.fever_5_days_or_more` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=child_amber.fever_5_days_or_more).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `child_amber.limb_or_joint_swelling` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=child_amber.limb_or_joint_swelling).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `child_amber.non_weight_bearing` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=child_amber.non_weight_bearing).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `pain_score` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=pain_score).answer.valueInteger` |  |  |  |  | yes |
| `pain_present` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=pain_present).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `injury` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=injury).answer.valueCoding` | cf:tri-state | yes / no / unknown |  |  | yes |
| `arrival_mode` | write | QuestionnaireResponse | `QuestionnaireResponse.item(linkId=arrival_mode).answer.valueCoding` | cf:arrival-mode | walking / private_vehicle / public_ambulance / private_ambulance / other |  |  | yes |

`not_mapped`: none. Every StructuredFacts leaf is read from or written to FHIR (test: `tests/test_fhir_mapping.py`).

## Reads (EHR to facts, provenance `EHR`)

- **Patient**: matched by `identifier` with system `https://careflow.example/fhir/sid/keypad-id` (the keypad digits); `birthDate` gives `patient.age_years` and `patient.age_months`; `gender` gives `patient.sex`.
- **AllergyIntolerance** and **Condition** where `clinicalStatus` is `active`; **MedicationStatement** where `status` is `active`. Each code becomes a `CodeFact` with provenance `EHR` and a `fhir` source.
- **Appointment**: today's appointments with status `booked` (check-in context for the routine and no-show personas).

## Writes

- **Encounter**: `status` in-progress, then finished; `class` v3 ActCode `AMB` by default (configurable per hospital); `priority` from the final level (convention below); `reasonCode` from the model-inferred complaint category.
- **Vital signs**: Observations with category `vital-signs`, LOINC codes and UCUM units: heart rate 8867-4 `/min`, respiratory rate 9279-1 `/min`, SpO2 2708-6 plus 59408-5 `%`, body temperature 8310-5 `Cel`, blood pressure panel 85354-9 with systolic 8480-6 and diastolic 8462-4 `mm[Hg]`, pain 72514-3 `{score}`. A nurse value replaces a device value at the same fact path; each Observation is written with the provenance of the value it carries.
- **NEWS2 total** (SNOMED CT UK 1104051000000101, `{score}`), **ACVPU** (1104441000000107 with the UK Core ACVPU answers) and **supplemental oxygen** (371825009 Patient on oxygen / 722742002 Breathing room air). A partial kiosk NEWS2 is written with status `preliminary` and a note naming the missing inputs, never as a full score.
- **Triage level**: one Observation, code `cf:triage-level#triage-level`, `valueInteger` = the nurse's final level (1 = most urgent), component `scale` from `cf:triage-scale`. A model suggestion is never written as a level: only the NurseDecision is.
- **QuestionnaireResponse**: `questionnaire` = `https://careflow.example/fhir/Questionnaire/careflow-intake|0.1.0`, items keyed by linkId = fact path. One response per answering source: the patient or parent (`source` Patient or RelatedPerson, tag `PATIENT_STATED`) and the nurse (`source` Practitioner, tag `NURSE_CONFIRMED`).
- **Flag**: one per Alert; status active, category clinical, code from `cf:rule-id`.
- **Task**: for `CHEST_PAIN_ECG_10MIN`, code `cf:task-kind#ecg_within_10_min`, status requested, intent order, `restriction.period.end` = `Alert.deadline_at` (raised_at + 10 min), focus the Flag.
- **Communication**: the nurse notification (category `notification`, about the Flag, payload = the fixed, non-PHI alert template).
- **Composition**: the doctor briefing, type LOINC 54094-8 Emergency Department Triage Note, one section per topic, sent as a `document` Bundle with the Composition first. Section text shows the provenance tag of every item (safety rule 4).
- **AllergyIntolerance** (write): only for an allergy the patient states, with `verificationStatus` = `unconfirmed`. What the patient says is never written as confirmed.

## Priority convention (a CareFlow convention, not a standard)

`Encounter.priority` uses the HL7 v3 ActPriority codes, but the mapping from level to code is **CareFlow's own convention**; no standard defines it:

| Final level | ActPriority |
|---|---|
| 1 | S (stat) |
| 2 | EM (emergency) |
| 3 | UR (urgent) |
| 4, 5 | R (routine) |

## Provenance (safety rule 4)

- Every written resource carries `meta.tag` codes from `cf:provenance-source` (`EHR`, `PATIENT_STATED`, `DEVICE_MEASURED`, `NURSE_CONFIRMED`, `MODEL_INFERRED`): one tag for a single-source resource (a device vital), one tag per source for a resource that aggregates several (Encounter, the briefing Composition). A Flag, Task or Communication carries the provenance of the alert's evidence facts.
- Each write batch also gets one **Provenance** resource with `target[]`, `recorded` and an agent by source: Device (`performer`) for the kiosk vitals device, Device (`assembler`) for a model, Practitioner (`author`) for the nurse, Patient (`informant`) for what the patient said.

## Questionnaire

`questionnaire-intake.json`: url `https://careflow.example/fhir/Questionnaire/careflow-intake`, version 0.1.0, status draft. Every linkId is its StructuredFacts path (groups use the group path, e.g. `chest_pain`). Types: `string`, `integer` (0-10 through the `minValue` / `maxValue` extensions) and `choice` (yes, no, unknown from `cf:tri-state`). `enableWhen` gates the chest-pain follow-ups (on `chest_pain.present` = yes), the child danger group (age under 16) and the NG143 amber group (age under 5). Items the nurse answers carry the extension `https://careflow.example/fhir/StructureDefinition/audience` = `nurse`. Text is English; Arabic arrives in M2-03 through the `http://hl7.org/fhir/StructureDefinition/translation` extension, as the title already shows (machine-written; not reviewed by a native speaker).

## UK Core STU2

Targets for a UK deployment: **UKCore-Patient** (NHS number identifier alongside the keypad identifier), **UKCore-Encounter** and **UKCore-Observation** (vital signs keep the LOINC codes above and may add the SNOMED CT UK codes as extra codings). ACVPU answers follow the UK Core ACVPU value set. Conformance tests against UK Core arrive in M4-02.

## UAE DoH / Malaffi

Abu Dhabi's Malaffi (DoH) and Dubai's NABIDH exchange HL7 v2.5.1 messages (ADT, ORU) today; CareFlow's HL7 v2 exchange through Open Integration Engine comes in M4-01. The FHIR table here stays the internal source; the HL7 v2 mapping is derived from it there.

## Open questions

1. **Supplemental oxygen observation pattern** (`vitals.on_supplemental_o2`, `verified: false`). The SNOMED CT codes 371825009 Patient on oxygen and 722742002 Breathing room air were verified on 26 Sep 2026 in the NHS England Clinical Observations guide (simplifier.net). Carrying them in `Observation.code` follows the INTEROPen FHIR-NEWS2 InspiredOxygen guidance, but the members of that value set (CareConnect-InspiredOxygen-1) could not be read on the same date. Confirm the value set, or switch to a coded `valueCodeableConcept` if UK Core defines one.
2. LOINC and SNOMED CT displays are taken from the pages verified on 26 Sep 2026 (hl7.org FHIR R4 vital signs profile; loinc.org 72514-3 and 54094-8; UK Core ACVPU value set; NHS FHIR-NEWS2 guidance). SNOMED CT UK edition codes (the `1000000` namespace) are UK-specific; a non-UK deployment maps them through the adapter.

## Verification log (26 Sep 2026)

| Code | Source |
|---|---|
| LOINC 8867-4, 9279-1, 2708-6, 59408-5, 8310-5, 85354-9, 8480-6, 8462-4 | hl7.org/fhir/R4/observation-vitalsigns.html |
| LOINC 72514-3 (Active) | loinc.org/72514-3 |
| LOINC 54094-8 Emergency Department Triage Note (Active) | loinc.org/54094-8 |
| SNOMED CT 1104051000000101 NEWS2 total score | nhsconnect.github.io/FHIR-NEWS2 (NEWS2 Observation guidance) |
| SNOMED CT 1104441000000107 ACVPU scale score; 371825009; 722742002 | simplifier.net NHS England Clinical Observations guide |
| SNOMED CT 248234008, 130987000, 300202002, 450847001, 422768004 | UK Core ACVPU value set (fhir.interweavedigital.com) |
| v3 ActPriority S, EM, UR, R | hl7.org/fhir/R4/v3/ActPriority/cs.html |
| v3 ActCode AMB | hl7.org/fhir/R4/v3/ActEncounterCode/vs.html |
| flag-category `clinical`, communication-category `notification` | hl7.org/fhir/R4 code system pages |
| provenance-participant-type | terminology.hl7.org CodeSystem provenance-participant-type |
