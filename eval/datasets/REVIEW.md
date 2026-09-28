# Automated dataset review

Reviewed by automated checks, not clinicians.

## Automated gate run 2026-09-27T09:17:24.120205+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| breathing-low-oxygen-no-infection | `b09ceacf22629e8e0d68a45f834fd017c66a6e3f8ac6ea16bdc37f329b2990fd` | rejected | Expected empty alerts but SpO2 90 triggers NG143_AMBER_FLAG for hypoxia if age < 5 months; age 60 years prevents this.; Expected empty alerts but SpO2 90 triggers SEPSIS_NEWS2 if NEWS2 total >= 5; scenario lacks NEWS2 data to support this. |
| breathing-mild-focused-screen | `e40642cac84cfa2b5a80806f6800804c5a9d06b29ebbd9c519bdb7440149a89c` | accepted |  |
| breathing-moderate-no-infection | `ff09dca8d610c2a5de034568d8ea02f189c419d958e7b6291d09d6e80f270107` | accepted |  |
| breathing-persistent-no-chest-pain | `30899c9d9a9e18e78e29268db0e5fd8c803ddbfc2aec5840b69cb9c557b40164` | accepted |  |
| chest-pain-localised | `142e38164ebb393e0de014d26442a8e56fa20e21d56a35e6fd8f8ea83e53d5c7` | accepted |  |
| chest-pain-no-breathlessness | `6b3e7a3749037f2091b0df303eacf5a2dbfd563171f3bd89b18978574b24eea9` | rejected | judge output did not contain exactly one verdict per scenario |
| chest-pain-radiating-arm | `7bc0472e19a4a4e8a2845f3ab0589795d34454f3d907ad11845c3c57aba81f5d` | rejected | judge output did not contain exactly one verdict per scenario |
| chest-pain-screen-negative | `462a9642ae9c4698e4101eb8540bdb86c876c8aaf662d2c477b95fedf2a77c51` | rejected | judge output did not contain exactly one verdict per scenario |
| chest-pain-screen-uncertain | `c464bdf640728ffbb930bf8c3ea2aedd97cd8390b6ff7c6ad54fbedfe576e0b7` | rejected | judge output did not contain exactly one verdict per scenario |
| chest-pain-sweating | `5734aa96d9dba92a7ddf7b3591c75c0f34d572593d3e86bde4d9527e398e369e` | rejected | judge output did not contain exactly one verdict per scenario |
| chest-pain-unsure-radiation | `a94950a0a0273df7717455eebfd8e8514e83c67dfd61378c54f2b0eff692e2ec` | rejected | Alert CHEST_PAIN_ECG_10MIN requires 'chest_pain.radiates' to be 'yes' or unanswered; scenario has 'unknown' which is not a trigger per rule logic.; Expected alert level 2 is unsupported; rule only raises critical alerts (level 1) or needed facts, never a draft level 2 label. |
| chest-pain-with-jaw-radiation | `178609e06aa91330c85786d1753e9b547614af359e62312d4389f7615e37c066` | accepted |  |
| child-feeding-and-urine-amber | `524adab4526725ceb8030b7f36cb9eaf27ea3f86c5f569773a75132ba3935092` | accepted |  |
| child-grunting-and-poor-feeding | `357dc83921e3156a3b673157c66733ff703ce0e82835d2f15cb4d551470f3940` | accepted |  |
| child-hard-to-wake-under-five | `ef31c3cefe3477ce4950a9860c62562866544a21c95a48d4947d0dc4cdf258f6` | accepted |  |
| child-non-blanching-rash | `2ab68b807385dcdf36be9c5c44beeeb709c8a1dd6784e270189cc6eaa2a14b41` | rejected | Patient age 4 years (48 months) exceeds the under-5 threshold (60 months) required for NG143 rule application.; Alert NG143_RED_FLAG is raised incorrectly because the child is older than 5 years per the rule logic. |
| child-older-hard-to-wake | `619a50e5f6ddd863ef32ded32fa769385f23a190c5ce2b33dc6ca664d9ab62c4` | accepted |  |
| child-parent-unsure-waking | `0800cf9c0355b877fc67d4aaa7a7ca4ef5f452b600d0780b7c24e469cd283dcf` | accepted |  |
| routine-01-wrist-ache | `c0f46a40b4b6e4c86938cba4fd959764a56ecf07e74622136c5097cd3cdc860a` | accepted |  |
| routine-02-ankle-soreness | `7a8575e0e656b46d02cbcf5bbe8ac39bb7c441c1535cb6a1df64e1ed5be06708` | accepted |  |
| routine-03-knee-ache | `1bfa1719f3d7c1027aa6d74017d1b1130df3e7793e17e262764d2d1807eb84d4` | accepted |  |
| routine-04-shoulder-stiffness | `1ea87565b66c62e0746c904d9692b8726b39abf23b6c0593922be7a088932af4` | accepted |  |
| routine-05-elbow-tenderness | `265d35895c6cc75a1e53da24a25bd42028c502328620e7d875c568afc71aca18` | accepted |  |
| routine-06-heel-discomfort | `4cfea8b756b91129ebacda885c45494025cdfac6fee75313607fccfa3164bd64` | accepted |  |
| routine-07-finger-ache | `e420f2845826926d4f25bbab70277616b330eaa8ad76887f7437bf9276cb10d6` | accepted |  |
| routine-08-dry-skin | `59d4f9053442e583d258afc8f066321479afad20ed1f55ee491e594cef3e00d5` | rejected | judge output did not contain exactly one verdict per scenario |
| routine-09-scalp-itch | `eaace05aae1e9fd06fdcf20516cdae690e27ff3d16da26d28c3a49c88f0fdc07` | rejected | judge output did not contain exactly one verdict per scenario |
| routine-10-flaky-skin | `2bf8bc764e1364a0fdd10464e1feb39be260e8a2bc7bef783b1ccf7010d4026a` | rejected | judge output did not contain exactly one verdict per scenario |
| routine-11-ear-itch | `261b30983547afc4ac6bd509f97db224e8f00e10e6af170bb99ef7b1c8d0fb24` | rejected | judge output did not contain exactly one verdict per scenario |
| routine-12-blocked-ear | `9ebe0cda8c9fdde775f1b77e0bcf520632f5d70f02427621b73639c88b333dc5` | rejected | judge output did not contain exactly one verdict per scenario |
| routine-13-bloating | `7c7260bb3d81066eb5961b1fc5f875c703f6265e74e2b3b1742efb763a802811` | rejected | judge output did not contain exactly one verdict per scenario |
| routine-14-constipation | `f030cb7b95d9e410d4ebfbf740b84aa34f961303d7990c7c0c4fda0bd1cecd32` | rejected | judge output did not contain exactly one verdict per scenario |
| routine-15-queasiness | `0cf7da32b60b495d3d3b9e3b1c929af907bf1e4ab5d6a43a28c3e54a3c611eed` | rejected | judge output did not contain exactly one verdict per scenario |
| routine-16-toenail-irritation | `fffac2c38cc3ea3e5f16bc215f9d843cdb3b02aed1356f012deb0cd2d1f107dc` | rejected | judge output did not contain exactly one verdict per scenario |
| routine-17-foot-blister | `e88674272c20f85ec4399a5dd078957099c37eaf4ca50150786e89b2dd1d785d` | rejected | judge output did not contain exactly one verdict per scenario |
| routine-18-dry-lips | `5f260913c33f1cfacdf6c7209d425279a212da191e0dd0a3db0603d2e5d64952` | accepted |  |
| sepsis-kiosk-below-threshold | `97ad4531acdfb767e01456480eff8290f3a3e1802f5b2da0abffc9043ba876b1` | rejected | Expected no alert but scenario describes partial NEWS2 4; rule requires 5 or more to alert.; Expected facts include severity 5, but rule ignores severity and uses NEWS2 subscores only. |
| sepsis-kiosk-high-fever | `f5a8b487939d9c2be3d34e5e91097a7c9d370c5399dd5af44af11c2e7b0c1695` | rejected | Expected alert but SpO2 93 is not <= 95 (threshold is 95, not 94).; Expected facts include severity, but rule ignores severity and uses NEWS2 subscores only. |
| sepsis-kiosk-threshold-five | `9e53109b78c5a424447a1fb1f55119ce39312cec20d802e8295bb6e909f3926b` | rejected | Expected alert but SpO2 93 is not <= 95 (threshold is 95, not 94).; Expected facts include severity, but rule ignores severity and uses NEWS2 subscores only. |
| sepsis-low-measured-temperature | `d2c92fb43f0029d60329d32246c559c561f0fa3be6a4c0ea39ccd958ed1c58a2` | rejected | Expected alert but infection evidence requires suspected infection or fever >= 38.1; 35.0 is not fever.; Expected facts include severity, but rule ignores severity and uses NEWS2 subscores only. |
| sepsis-measured-fever-denied | `c827a6b0bf8850a5dfc63004119fb43eadbc19a3134130a8fdfce733c66b1e0b` | rejected | Infection evidence requires patient-reported fever or rigors; patient denied both, so no infection evidence exists.; Without infection evidence, SEPSIS_NEWS2 cannot be raised per rule logic. |
| sepsis-nurse-completes-high-score | `dff9f82b9fef4c3820d0e59db737a373213d890708e90c0f256b0e3597befd57` | rejected | Infection evidence requires patient-reported fever or rigors; patient denied both, so no infection evidence exists.; Without infection evidence, SEPSIS_NEWS2 cannot be raised per rule logic. |
| sepsis-nurse-confirms-threshold | `bfda7697e1d4dacefb1a2d6a7261d395c813329933cf656551e636ff22473b15` | rejected | Infection evidence requires patient-reported fever or rigors; patient denied both, so no infection evidence exists.; Without infection evidence, SEPSIS_NEWS2 cannot be raised per rule logic. |
| sepsis-rigors-with-normal-temperature | `c74afaf291604de67e8e1670e2992aff11906e7ffed3728120324a2bc4f52123` | rejected | Infection evidence requires patient-reported fever or rigors; patient denied both, so no infection evidence exists.; Without infection evidence, SEPSIS_NEWS2 cannot be raised per rule logic. |
| stroke-arm-positive | `817f15cb5b401fdeca536d0b2574f0cbd7c4400c37f455a1ae82c4e41dbbfe76` | accepted |  |
| stroke-face-positive | `3558cb7a93cf81db44fcfed3dc9729a39f4eccf31d299cb5e6a39f5c2c161184` | accepted |  |
| stroke-last-known-well-unknown | `65aa89276da6d0a81511f4760ca34bd91d647a036b4ec460679a90261e493b54` | accepted |  |
| stroke-multiple-signs | `ce68f652d6b46c6315dbed4bbd794de42c832b88aab06bd552f3e98fb0da7c06` | accepted |  |
| stroke-screen-unsure | `8692181a7e9ba764ca3f09daada79e64bd254a7bdcb5342b95b97dd4ad7e241c` | accepted |  |
| stroke-speech-positive | `b656b231dc532fcc8c5f2ac2f6e12199ae43b7e9960a2026e78c9d3a2baab76b` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate run 2026-09-27T09:34:10.982377+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| breathing-low-oxygen-no-infection | `18a56227856cc763c44192364799c1e0f8ac2227e7b60c5329ae125e4124957a` | accepted |  |
| chest-pain-no-breathlessness | `6b3e7a3749037f2091b0df303eacf5a2dbfd563171f3bd89b18978574b24eea9` | accepted |  |
| chest-pain-radiating-arm | `7bc0472e19a4a4e8a2845f3ab0589795d34454f3d907ad11845c3c57aba81f5d` | accepted |  |
| chest-pain-screen-negative | `462a9642ae9c4698e4101eb8540bdb86c876c8aaf662d2c477b95fedf2a77c51` | accepted |  |
| chest-pain-sweating | `5734aa96d9dba92a7ddf7b3591c75c0f34d572593d3e86bde4d9527e398e369e` | accepted |  |
| chest-pain-unsure-radiation | `bf7a815ea15d594fe4351f4cceeee02eaa552fadfbce0af151ec5a62be249164` | accepted |  |
| routine-11-ear-itch | `261b30983547afc4ac6bd509f97db224e8f00e10e6af170bb99ef7b1c8d0fb24` | accepted |  |
| routine-12-blocked-ear | `9ebe0cda8c9fdde775f1b77e0bcf520632f5d70f02427621b73639c88b333dc5` | accepted |  |
| routine-13-bloating | `7c7260bb3d81066eb5961b1fc5f875c703f6265e74e2b3b1742efb763a802811` | accepted |  |
| routine-14-constipation | `f030cb7b95d9e410d4ebfbf740b84aa34f961303d7990c7c0c4fda0bd1cecd32` | accepted |  |
| routine-15-queasiness | `0cf7da32b60b495d3d3b9e3b1c929af907bf1e4ab5d6a43a28c3e54a3c611eed` | accepted |  |
| routine-16-toenail-irritation | `fffac2c38cc3ea3e5f16bc215f9d843cdb3b02aed1356f012deb0cd2d1f107dc` | accepted |  |
| routine-17-foot-blister | `e88674272c20f85ec4399a5dd078957099c37eaf4ca50150786e89b2dd1d785d` | accepted |  |
| sepsis-kiosk-below-threshold | `27e7c6a7aa0711033b772304aefdb02cabd198737c9890d7d489cbc58990d0ae` | accepted |  |
| sepsis-kiosk-high-fever | `d238e6fc2cc98b4fce7c6e36e7809ea46854c09b432459afbefdd80c308629a9` | accepted |  |
| sepsis-kiosk-threshold-five | `4e20a35bfa34c2453f6ab929f72d193abf72ed4afcba913af0b10cf406376990` | accepted |  |
| sepsis-low-measured-temperature | `45299522898e8e86fe4dec6123d7c88abec7411a8079bec9e6f986b1b28057d1` | accepted |  |
| sepsis-measured-fever-denied | `b11e62f2e90feca489df0e582b82faa43290eae725ab4b8519dfb22a16ae7652` | accepted |  |
| sepsis-nurse-completes-high-score | `e870c68d2c36a67578cb8e42da69ce65ee202fb93c75946ecb88352a738c1620` | accepted |  |
| sepsis-nurse-confirms-threshold | `d738428133ae4bc211cbc54c9ec9dbebaec43fb4302aebaeb9d4652f7553c420` | accepted |  |
| sepsis-rigors-with-normal-temperature | `934104f2e5437758e8c4d2dee31c89c7a2fffdca1dc096b1aa0e13264307661c` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Interrupted-run audit recovery

Five approvals from the interrupted single-case Qwen run were reconstructed from the retained raw verdict receipts and byte-identical approved scenario files. Their hashes, all four checks and both endpoint observations were verified. The recovered rows in `review-log.jsonl` carry `audit_recovery` metadata; no new model approval was invented.

## Automated gate decisions 2026-09-28T07:02:51.101149+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-child-01 | `e20812eef636870f246de66d374897a889dc2ed1ae488d023b69d5c2e7a37949` | rejected | answer 2 (child_danger.hard_to_wake): back-translation "I don't know, I don't know what to say" vs gloss "I don't know, I can't tell.": meaning differs (Uncertainty vs inability to speak) |
| ar-gul-routine-16 | `e2433cb02fd0662bee005d7dabdc1276d25f48a0503b720a667122b310750fcd` | rejected | answer 1 (chief_complaint): back-translation 'I have pain in the molar' vs gloss 'I have pain in the tooth.': meaning differs (Specific tooth vs molar); answer 4 (pain_location): back-translation 'In the molar' vs gloss 'In the tooth.': meaning differs (Specific tooth vs molar); chrF 28.5 < 40 |
| ar-gul-stroke-03 | `b46a99c3d2c0ffc75f9d73b49423e10df5192562199cf3f11f54cfc8e804dda3` | rejected | answer 3 (fast.arm_weakness): back-translation "No, I don't have weakness" vs gloss 'No, I do not feel weak.': chrF 39.3 < 40 |
| ar-lev-routine-16 | `0c79dd2a080b004c174ef33e9a29b8360aa2463e0aac34c9b7ad3ef380ab2d4f` | rejected | answer 1 (chief_complaint): back-translation 'I have pain in the molar' vs gloss 'I have pain in the tooth.': meaning differs (Tooth vs molar) |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:03:20.185408+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-breathing-01 | `6e06f45723e1e01659d728d10015bf6f8b019132ee991e907e927bb11715be96` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:03:29.205999+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-breathing-02 | `73e301c583cc11b5213a418b9d9ff53394290e7553b31ac7c95cfb8764d1e161` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:03:41.851669+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-breathing-03 | `ea3a8f18b708b25cae7fc00caae2e5b708be1b8c8832346053d2d95001735e30` | rejected | Alerts expected to be empty, but SEPSIS_NEWS2 triggers critical due to NEWS2 total of 5 (fever 36.0, HR 110, SpO2 94, shortness of breath, severity 6).; Alerts expected to be empty, but FAST_STROKE triggers critical due to shortness of breath being mapped to speech difficulty symptom. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:03:50.751518+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-breathing-04 | `7820ecbfb7f2412d7ebd2e7a1c6486aeefe47f40935669946adb745a431da212` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:04:03.555851+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-chest-pain-01 | `b17abd6d4f1264dc30b4d49068a2d24807c956f3f4488e5b0d19e6f6fcbf37dc` | rejected | Expected alert level 2 does not match rule CHEST_PAIN_ECG_10MIN which defines severity as critical (level 1).; Alerts list contains a non-existent rule ID 'CHEST_PAIN_ECG_10MIN' instead of the defined 'CHEST_PAIN_ECG_10MIN'. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:04:15.535876+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-chest-pain-02 | `09ac490c9b3bfdd6fc42c6a4430a0674b6753d30d0f8d1576dc99bf73f5bbd69` | rejected | Expected facts include 'chest_pain.present': 'yes', but patient text states 'Yes, the tightness is in my chest' which is ambiguous without a direct 'yes' to the chest pain question.; Alert CHEST_PAIN_ECG_10MIN requires chest pain onset within 10 minutes of arrival or recording, but patient text states onset was 'Two days ago'. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:04:26.985378+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-chest-pain-03 | `8cb1665b1ba8f1b332d01fff6b7f76b95a1a4baeabc5a3d3bcfc3d27f356ea3e` | rejected | Expected alert CHEST_PAIN_ECG_10MIN requires chest pain onset within 10 minutes of arrival; patient onset is 'Two days ago' (1440 minutes).; Expected facts include 'chest_pain.present' as 'yes', but patient text states onset was two days ago, contradicting current chest pain. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:04:38.058831+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-chest-pain-04 | `ba3083c0fdfcd87eab2a2a494f139ec1ba7b3fbd1ad90a1808527c7c91f5d3a5` | rejected | Expected facts include 'pain_location' and 'pain_present', but patient text only states chest pain presence, location, and sweating.; Alert CHEST_PAIN_ECG_10MIN requires chest pain onset within 10 minutes of arrival; patient onset is 'about a week ago'. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:04:50.062876+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-chest-pain-05 | `a320f44123aad12daeabb4b0417015cd23ca916ebec8d06297e719368b39a2df` | rejected | Expected chest_pain.present is 'yes' but patient text states 'Two days ago' onset, contradicting 'now or last hours' rule.; Alert CHEST_PAIN_ECG_10MIN is critical but expected.level is 2; rule defines severity as critical, not level 2. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:05:01.427884+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-chest-pain-06 | `cc34fca7dea02484ffc819fd3ee4e1a5b49314ed573218983dde91edbd8b7e6a` | rejected | Expected facts include 'chest_pain.present': yes but patient text states onset was 'Three days ago', contradicting the rule's requirement for present pain.; Alert CHEST_PAIN_ECG_10MIN is critical and requires pain within 10 minutes of arrival or onset; 3-day onset does not support this alert. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:05:13.921972+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-chest-pain-07 | `8128d296ef50b698f4813c7ea965fe34ef39a2dec83c6a631fc6826fa6810854` | rejected | Expected chest_pain.onset 'Three days ago' contradicts the rule requiring pain 'now or in the last hours' to trigger the alert.; Alert CHEST_PAIN_ECG_10MIN is raised despite the onset being 3 days ago, violating the 10-minute door-to-ECG deadline logic. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:05:21.864659+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-chest-pain-08 | `687cf97b479941f1b8e832c3fb8c9b0cbc543da479619e851018fbf99e1e2356` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:05:32.562616+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-child-02 | `020f82784b6e75dbdf2d70fd5f22d9134f73825cbc4fa1de73f2949cdfceaa16` | rejected | Alert NG143_RED_FLAG requires age < 5 months; patient is 2 years old (24 months).; Alert requires red features like hard_to_wake or seizure; patient has no red features. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:05:44.000539+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-child-03 | `04cb3e6cd08ebc21922e232785a208e98923d37f8db57c09ffc1fbc7deb83d36` | rejected | Patient is 1 year old (12 months), which is under 5 months (60 months). NG143 rules do not apply to children under 5 months.; Expected alert NG143_AMBER_FLAG is incorrect because the child is too young for the NG143 rule to trigger. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:05:55.716468+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-child-04 | `afa8f71e27e5e23974ffaf434b93038107af0ad1ebcecdf1e4c5e9333b9a2b69` | rejected | judge reviewed-content hash does not match the supplied scenario |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:06:06.660329+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-child-05 | `5515b60474f893f3ef5cb986521dbdacdf4a9ff663df608bfe7039bf6fcb7268` | rejected | Age 13 years (156 months) exceeds the 5-15 year range for CHILD_HARD_TO_WAKE; rule returns no alert.; Expected alert CHILD_HARD_TO_WAKE is unsupported because the patient is outside the rule's age criteria. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:06:17.048638+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-child-06 | `540dd63daaa9f54091f5b96cda70ef02c4a4a568943209c08fa3d514dd3ca57b` | rejected | Age 11 years (132 months) exceeds 16 months threshold; CHILD_HARD_TO_WAKE rule returns no alert for this age.; Expected alert CHILD_HARD_TO_WAKE is incorrect for age 11; rule logic requires age 5-15 months to trigger. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:06:24.964001+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-routine-01 | `90363233ff789ea94b4b433bdfe535fc604883c42146b72fd1a172bfd272a1ca` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:06:33.154426+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-routine-02 | `bac6c6d11f9fc467580369c3b65bc68ba48f6fd02cc2cfde39d1a8097381b382` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:06:41.317885+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-routine-03 | `7434eb997af2d80e4f09026a8ef88bc6c0f03533065baa4000aa84ad9213dd8c` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:06:49.739535+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-routine-04 | `8d9da92104670dc7e5f5405b66d8aae14e0e988d9d59e5dd90c61861ecbc8f2b` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:06:57.883892+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-routine-05 | `c14f86359133e814cc3185af8186a39e5db6d41ac4b207e577600908c05b0f36` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:07:06.547634+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-routine-06 | `8f93007eda3bdf4e7d053ac7acb54011fc391b1d5ed746c00dd47e8b85802f87` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:07:14.604013+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-routine-07 | `b26a2bde5d5816e85a3b590fd263f11e8c3e8c37e9b733087f64b16fb965204b` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:07:22.983688+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-routine-08 | `9055ef86bda585873be74096ea5724b08e50d37b0a99e008e9eeec6349a94951` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:07:30.938724+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-routine-09 | `4dee2c41bd0fc9ec6a97538082f85966d23ead145b4520b93c4750da4679114c` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:07:39.481540+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-routine-10 | `3dc5b42cb29a574352a9dffea2132e35422c571b643b996c1dd358c4bf4bb6a0` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:07:47.619616+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-routine-11 | `bd4ed7537ff8078b9a48f40f6b6cd3b07a89fd0747f01d85258852b43842d118` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:07:56.520025+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-routine-12 | `7d8c0dc876b54866ab9f805304c784d795992027ffa54a3d69b5447941ebf46a` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:08:04.905677+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-routine-13 | `17714df010bf995b851505cfe886ff9cbec5f9f508d0e64bb10ad4d1e1176aed` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:08:13.596069+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-routine-14 | `dc281952586d0d54f31de2cf368b6037f58b605e505e66e3dc16832a7b1fccf1` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:08:21.883418+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-routine-15 | `dee682c6d1b491e6ac5aee0b5be1349e6734628b5b15af3cbd5ef5da936eef0a` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:08:30.029243+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-routine-17 | `71fdced92715792a075d33ad8b5e453f321ab3d719f1bbc7e52d3cbdb484106b` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:08:38.323370+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-routine-18 | `2cba451dc3c7d482ee809e88e2583bbb6f74fefe6eba9389749fd8d717d1bc57` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:08:49.487479+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-sepsis-01 | `6f7ba3c62295f38325363acd58b321527cacc4aabd2be7736c6be46ff394db55` | rejected | Alert SEPSIS_NEWS2 requires NEWS2 total >= 5; scenario provides no NEWS2 data to meet threshold.; Expected level 2 is invalid; SEPSIS_NEWS2 is critical (level 1) per rule definition. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:09:00.388657+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-sepsis-02 | `6ce36b7af65f23bb2fc6f5cda65aab4623227b65a0ead809b6e4dcf8ffd77a03` | rejected | Alert SEPSIS_NEWS2 requires NEWS2 total score >= 5; scenario provides no NEWS2 inputs.; Alert level 2 is invalid; SEPSIS_NEWS2 is always critical (level 1) per rule definition. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:09:12.743458+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-sepsis-03 | `7ee787c927654918b0e9bd0c6e3cba0b2c6e423236b2a37624238052d26865dd` | rejected | Expected facts contradict patient text: 'I don't have a fever' vs expected 'no' (supported), but 'not sure' rigors maps to 'unknown' not 'unknown' in expected.; Alert SEPSIS_NEWS2 requires NEWS2 total >= 5; scenario provides no NEWS2 data, so alert cannot be raised per rule logic. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:09:24.030170+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-sepsis-04 | `4a83ece6544574a3e6529b3adf121fd21e5b73c7624e51761c5fbec55584cab0` | rejected | Expected level 2 but rule SEPSIS_NEWS2 only raises critical (level 1) alerts; level 2 is not defined for this rule.; Expected alerts list contains SEPSIS_NEWS2 but expected level 2 implies a different rule or mapping not supported by the rule. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:09:35.331449+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-sepsis-05 | `c89bc03f8df3a80dff7cbc20f0caeecd4fbf2ff08e48e36a3008f5eff9db3e1d` | rejected | Expected facts include 'I feel very unwell' but patient text states 'I feel very sick' (gloss mismatch).; Alert SEPSIS_NEWS2 requires NEWS2 total >= 5; provided vitals (NEWS2 4) do not meet threshold. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:09:46.299867+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-sepsis-06 | `efddb6a0189308721550b77e98c4954386ce118ed342676ea1702f2444ab0894` | rejected | judge reviewed-content hash does not match the supplied scenario |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:09:57.556350+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-sepsis-07 | `dfea8528c6568dd5e7757a5e5f6550765290adfce5dbea595592242068701e29` | rejected | Expected facts include 'I feel very unwell' but patient text states 'I feel very sick today'; meaning mismatch.; Expected alerts are empty but temperature 38.2C meets sepsis criteria (>=38.1) with suspected infection (rigors unknown). |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:10:09.291069+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-sepsis-08 | `2fe33bdd13d43e791555c12558becc054cfedf7768042dcd24f9aed585637da8` | rejected | Expected alerts is empty, but infection evidence (fever, rigors) and NEWS2 total 5 trigger a critical SEPSIS_NEWS2 alert per sepsis.py.; Expected level 4 is invalid; the rule only defines critical (4) or high (3) severity, and no level 4 exists. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:10:20.848532+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-stroke-01 | `6d5e99e40a8360a97f6acc645caef11ff74faedf40a5c4989929a4827b910cbf` | rejected | Expected fact fast.speech_difficulty is 'yes' but patient text 'tongue is heavy' does not match the FAST sign definition.; Alert FAST_STROKE is raised for 'tongue is heavy' which is not a FAST sign; only 'face drooping' triggers the alert. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:10:32.156674+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-stroke-02 | `6203ad70d0c547fe9fcfa429ca5e469efcf116d4c7161a49fa361cdb33600a90` | rejected | Alert level 2 is invalid; FAST_STROKE is defined as critical (AlertSeverity.critical) in the rule code.; Alert evidence is incomplete; rule requires at least one FAST sign (face/arm/speech) to be yes, but face and arm are no. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:10:43.312168+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-stroke-04 | `3af3374d06695863fbd6d7c2d18959355c4880a56ab45ffaeb3c7bfc27ae6dcf` | rejected | Expected fact 'fast.speech_difficulty' is 'yes' but patient text says 'tongue is heavy', not 'speech difficulty'.; Alert FAST_STROKE requires all three FAST signs (face, arm, speech) to be yes; patient has unknown onset. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:10:53.603004+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-stroke-05 | `b526e91405e49dff5761e87a123164d92695e56f93a8e1f5a64de09d496ac3db` | rejected | Alert level 2 is not valid; FAST_STROKE is critical (level 1) per rule code.; Alert evidence missing; rule requires FAST signs (face/arm/speech) to be yes. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:11:01.517465+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-stroke-06 | `352ca382cb66f38eedfd9e1720f030cf2bf6d2ac0100b27f6ac70dd818d56b97` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:11:09.625786+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-breathing-01 | `b8cf352454850d932c9bd9b958009835b1d53794e4fe3c3db6cea409108fca5f` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:11:18.135822+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-breathing-02 | `ad224b3aa29193076af27ed263b69ffef954d9e3240606c6c814edce5b03460e` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:11:38.589389+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-breathing-03 | `4ef0e93eb556baa928435c469a6451ef0545a9431d9473f63f3136c693a115b2` | rejected | Expected alerts is empty, but SEPSIS_NEWS2 should trigger because NEWS2 total is 5 (respiratory 4, heart rate 110, SpO2 94, fever no, infection no, severity 4).; Expected level is 4, but the rule engine would raise a critical SEPSIS_NEWS2 alert, not a synthetic level 4. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:11:58.952575+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-breathing-04 | `6ee86c897a09649d92f8ee262eb45eec966f4cd7cae22cd9fcc66ad3469f2097` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:12:09.767406+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-chest-pain-01 | `60d576788d5fb1090924e5d5d55c698457d6a20e0be375613bd152105fd9e665` | rejected | Alert severity is 'critical' in the rule, but expected level is 2 (high).; Alerts are not consistent with the rule's defined severity. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:12:23.493083+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-chest-pain-02 | `a0c8f7a749a230e8e507a214608f3a81ab6207c49e06c9371451cddfcae4a49f` | rejected | Expected alert level 2 does not match rule CHEST_PAIN_ECG_10MIN which defines severity as critical (level 1).; Alerts list contains only the rule ID but expected level 2 implies a different severity not present in the rule. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:12:36.011643+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-chest-pain-03 | `cb697b57d2d259fc2d7063c5e73f85d7a0219207618c2c083650afa5d6f40fa1` | rejected | Expected alert level 2 does not match rule CHEST_PAIN_ECG_10MIN which defines severity as critical (level 1).; Alerts list contains only the rule ID but expected list includes a severity level not present in the rule definition. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:12:50.165926+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-chest-pain-04 | `0bb1da794cb9ab922fa5fe944d99c7aa2d3a42883f57f11123a4cfddb0c25a13` | rejected | Alert severity is 'critical' in the rule, but expected level is 2 (high).; Alerts list contains only the rule ID, but the rule outcome includes a 'chest_pain_screen' needed fact. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:13:00.746187+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-chest-pain-05 | `91a91684a72163769e73ecc0f1c8768efc9dfb00574636cea96868fa165373ae` | rejected | Alert severity is 'critical' per rule, but expected level is 2 (high).; Alert expected in scenario but rule requires chest pain onset within 10 minutes of arrival. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:13:15.828576+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-chest-pain-06 | `e9743b64af3b62bbad026534fe3b8d9cb12ae17ffb46d801b1a2d95a8b7060ed` | rejected | Expected alert CHEST_PAIN_ECG_10MIN requires chest pain onset within 10 minutes of arrival or recording; patient onset is 'Two days ago' (1440 minutes).; Expected facts include 'chest_pain.present': 'yes' but patient text states 'Two days ago', contradicting the rule's requirement for current or recent pain. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:13:28.380820+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-chest-pain-07 | `f7568f208c58c3a6f5b1ce3d3e54c5e5eacca51fcaee13ca9e7396578806fb1a` | rejected | Expected alert CHEST_PAIN_ECG_10MIN requires chest pain onset within 10 minutes of arrival; patient onset is 3 days ago.; Expected facts include chest_pain.onset 'Three days ago' which contradicts the rule's requirement for recent onset to trigger the alert. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:13:39.377715+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-chest-pain-08 | `94858a2e951aa0ebfa13856b225b7c4ce25a9159591fbae3dd06f79ae507a358` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:13:50.423346+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-child-01 | `cef6f10b55af58d6d6576ad0e3435e09c13203a81465c6ea6787acde8df9a957` | rejected | Patient age is 2 years (24 months), which exceeds the under-5 threshold; NG143 rules should not trigger.; Expected alert NG143_RED_FLAG is inconsistent with the rule logic for a 2-year-old child. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:14:04.151575+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-child-02 | `4ab18ebe16fc708459903ed82d6484f3a25fdeecc253c901b6701f1f69f591b8` | rejected | Alert NG143_RED_FLAG requires age < 5 months; patient is 2 years old (24 months).; Red flag 'non_blanching_rash' is not in the PARENT_RED list for red alerts in this rule. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:14:15.451024+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-child-03 | `d11b3effe19ce041926040889079311c76ea6c6fd3be43efaa7e93f1b60cd685` | rejected | Patient age 4 years (48 months) exceeds the 5-year threshold for NG143 rules, which only apply to children under 5.; Expected alert NG143_AMBER_FLAG is invalid for this age group; the rule returns no alert because the child is older than the guideline covers. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:14:29.414040+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-child-04 | `1a8c946cba6d6359c141524a8ca7ba74f798f159b0ac87058aa9f0e050d66b56` | rejected | Age 3 years (36 months) exceeds the under-5 threshold; NG143 rules should not trigger for this age.; No vital signs (SpO2, temp, HR, RR) are provided to support an NG143_AMBER_FLAG alert. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:14:41.350047+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-child-05 | `896d14899e5c693d032dc6d07ff5025a7c590988207831920395f62f4a6b5be5` | rejected | Age 13 years (156 months) exceeds the 5–15 year range for CHILD_HARD_TO_WAKE; rule returns no alert.; Expected alert CHILD_HARD_TO_WAKE is raised in scenario but rule logic suppresses it for this age. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:14:54.969548+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-child-06 | `96f2d85cac587338fede2ba2f4a33e8ecb07fb0b73cbdf6a535fe362d628a4cb` | rejected | Patient age is 9 years (108 months), which exceeds the 168-month threshold for CHILD_HARD_TO_WAKE alerting.; The rule requires age 5 to 15 years; a 9-year-old is outside the target age range for this specific alert. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:15:03.621239+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-routine-01 | `1d36c567970ccf91706acac4cfacf0b29d2fd5e3ad649c1e87f755ae2a8e280e` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:15:14.693639+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-routine-02 | `bd185b0f7673ba3ed1fe722efe4ebb3d88538a9be48d21d025136e64fe4c1500` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:15:22.987678+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-routine-03 | `6add53bb2863fcda74c9dced852d171ec18b945acb7760bba4240b313d868626` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:15:34.249449+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-routine-04 | `4e74d6aeb27c31e72aeb5852807693b06829d29f100dba4b9c12cc4a0f4b7b2a` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:15:42.581132+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-routine-05 | `94ba0e6b92ac23bff90ca7ba609f2810434929114f8faa84077e3f43fd71d272` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:15:54.893625+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-routine-06 | `e5f61b6a32c491d8439ad45c49acc345a6e5f72f70216d79dcc5c870c9d04a40` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:16:03.141958+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-routine-07 | `99e42fad10599637981a3c5f572f01bca29e5469849152a3a2ba1e88380ce30c` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:16:15.188976+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-routine-08 | `6d2c9d402eebbff9b25e7a2027efa006ccfd45deb9084c634d551bdbec37cb68` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:16:23.108553+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-routine-09 | `9df149a2345db880bed3c90aa2b6faf69f0e152de69f3bb8f73eccf84ae451ab` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:16:34.806350+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-routine-10 | `0e5e17f60d59bc636ac4d55b473306c3aea9a19e64e7e1140bb9aaa2dcd9ca75` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:16:42.926158+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-routine-11 | `07a6fc8fbadcd8280e71c9188c205ab4100fcb398178cc5f74bb5089d52b0836` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:16:51.734322+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-routine-12 | `5148a47be5d1a10999664e018fe4681c720799f24783c884735925032b30e5ea` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:17:00.041174+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-routine-13 | `3df17a43fa5c941243e20409de6688f651518b0a04ba700cd998ade4be24204f` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:17:08.315412+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-routine-14 | `05b97e463e7bd6a2800af988d835e5250e2c1a9fc9786affa8467fb3546175c8` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:17:16.190399+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-routine-15 | `5426ad52613b509d38bbbda3fbfa777a747828637387f79cdc140c6159bfccaf` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:17:24.369938+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-routine-17 | `dadfd2dd5afe6b1f68a6d4b8b962db7cbcce746d101748a7e422d4e290617976` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:17:32.126194+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-routine-18 | `6abb5f443210a9305b5f194866779f53decfea1fb8948f72b5e7bd2af5103eb7` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:17:42.896046+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-sepsis-01 | `cc8e6922e73ccb847bfce62f620edefbca1d837835588c597f50814541208e01` | rejected | Expected level 2 but rule SEPSIS_NEWS2 only raises critical (level 1) alerts.; Expected alert SEPSIS_NEWS2 requires NEWS2 total >= 5, but calculated total is 3. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:17:54.522274+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-sepsis-02 | `22eff1a57ec11fa834fd7fb3b999b5939c367349eb2f3bfbb8ae1400a7d8ccdf` | rejected | Expected facts include 'I feel very unwell' but patient text states 'I am very sick' (gloss mismatch).; Expected alerts is empty, but sepsis rule triggers critical alert due to NEWS2 total of 5 (hypoxia 93% + tachycardia 135). |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:18:05.710638+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-sepsis-03 | `48abef36d796be84dcb7f71af865966e38b981243f956cfe26ffb5e189869733` | rejected | Alert level 2 does not match rule SEPSIS_NEWS2 which is defined as critical (level 1).; Alerts list contains SEPSIS_NEWS2 but expected level 2 is invalid for this rule. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:18:16.707267+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-sepsis-04 | `efc1ccf7a8de0005fb41b59464812539fbfb8266f5689fec9ea11982775f00e3` | rejected | Expected level 2 (high) but rule SEPSIS_NEWS2 only raises critical (level 3) alerts.; Expected alert SEPSIS_NEWS2 requires NEWS2 total >= 5, but only 2 subscores are present. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:18:27.788630+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-sepsis-05 | `abc2436ec8ec119d259aaa0cab18dbd1e630e140b84f89214c1377bad62dfa11` | rejected | Expected alert level 2 does not match rule SEPSIS_NEWS2 which always raises AlertSeverity.critical.; Expected alert list contains SEPSIS_NEWS2 but rule logic requires NEWS2 total >= 5, which is missing. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:18:40.860397+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-sepsis-06 | `a6555755f9184ff71e90c606bde1b31aa9d60c8a84ecdedb3872dc5e63199619` | rejected | Expected facts include 'infection.rigors' as 'unknown', but the rule requires 'yes' or 'unknown' to trigger; 'unknown' alone does not trigger SEPSIS_NEWS2 without other infection evidence.; Alert level 2 is invalid; SEPSIS_NEWS2 is defined as critical (level 1) in the rule code, and the expected level 2 does not match the rule's AlertSeverity.critical. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:18:52.335560+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-sepsis-07 | `2454c0f1e8ba95a9b83f7e6d49de3a4f6a9a4797c0b6ddbbd376423e4de4f764` | rejected | Expected facts include 'I feel very unwell' but patient text only states 'I'm very sick' (chief_complaint).; Expected alerts are empty, but infection evidence (fever 38.2C) and NEWS2 total (5) trigger a critical SEPSIS_NEWS2 alert. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:19:00.995215+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-sepsis-08 | `2d377b23fe9b95c6d5b0cd1bf4896df7f64342d756dbc16816e1a55a63cbd58d` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:19:11.242262+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-stroke-01 | `634802030b14bad900782e672cd5383bafa7affed9cfc34ad42a746ec3d7dfb8` | rejected | Expected alert FAST_STROKE requires at least one FAST sign (face/arm/speech) to be yes; scenario has only speech difficulty yes.; Expected level 2 is invalid; FAST_STROKE rule always raises AlertSeverity.critical (level 1). |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:19:22.594682+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-stroke-02 | `e25ebaaaa752588846de5b1114d47e9d87985614ead54b06dcd2015f31156abf` | rejected | Expected fact 'fast.speech_difficulty' is 'yes', but patient text states 'tongue is heavy' (tongue heaviness is not a FAST sign).; Alert FAST_STROKE requires at least two FAST signs; scenario has only one (face droop) and one unsupported symptom. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:19:33.690651+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-stroke-03 | `27af46b0206dab8531bd45d41627644f17bfac943dae4f45937fdd7a0cd2a3cc` | rejected | Expected facts include 'fast.face_droop': yes, but patient text and gloss explicitly state 'No' for this sign.; Alert FAST_STROKE requires at least two FAST signs (face, arm, speech), but only arm and speech are positive. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:19:44.242551+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-stroke-04 | `162faeae0bc767df8ed8d4d7322687beebbf3f4f8025741880d43cc7f7769de6` | rejected | Alert severity is critical per rule, but expected level is 2 (high).; Expected facts include 'My speech is slurred' which is not in the patient text. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:19:55.198177+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-stroke-05 | `6f951d0671517f89b4c2d37fa9cedcf6d6ecc1aa96c79b189caaf3f49e60cc06` | rejected | Alert level 2 is invalid; FAST_STROKE is critical (level 1) per rule code.; Expected facts include 'fast.last_known_well' but rule only asks for it if FAST signs are missing. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:20:04.158260+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-stroke-06 | `d214870f6e5978f22ae52125f8dab8a7f7d1fd6a5a5b471b6f02743d4a45e8cd` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:20:58.905642+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-child-01 | `74ec3013a938fbd4ca6d2b5b7bdf9d89c63a0c12cde27759b728d0a46ffe2800` | rejected | no passing back-translation record for this exact content: the local review runs the M2-01 check first |
| ar-gul-routine-16 | `2ee8f46f8522b5970ee3f8d0b2d996f513140fb1eea8b2d00aa46c3136c58bcf` | rejected | no passing back-translation record for this exact content: the local review runs the M2-01 check first |
| ar-gul-stroke-03 | `4a12cc72a5777c53f917ec921f162adc4c98e04f208a6bf02423abf19f2b7498` | rejected | no passing back-translation record for this exact content: the local review runs the M2-01 check first |
| ar-lev-routine-16 | `6aadda7b6dc3eea383373d477ce1f0889e4e62ff16257f95f40c4bd883dd9f40` | rejected | no passing back-translation record for this exact content: the local review runs the M2-01 check first |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:21:20.831986+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-breathing-03 | `ea3a8f18b708b25cae7fc00caae2e5b708be1b8c8832346053d2d95001735e30` | accepted |  |
| ar-gul-chest-pain-01 | `b17abd6d4f1264dc30b4d49068a2d24807c956f3f4488e5b0d19e6f6fcbf37dc` | accepted |  |
| ar-gul-chest-pain-02 | `09ac490c9b3bfdd6fc42c6a4430a0674b6753d30d0f8d1576dc99bf73f5bbd69` | accepted |  |
| ar-gul-chest-pain-03 | `8cb1665b1ba8f1b332d01fff6b7f76b95a1a4baeabc5a3d3bcfc3d27f356ea3e` | accepted |  |
| ar-gul-chest-pain-04 | `ba3083c0fdfcd87eab2a2a494f139ec1ba7b3fbd1ad90a1808527c7c91f5d3a5` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:21:47.398596+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-chest-pain-05 | `a320f44123aad12daeabb4b0417015cd23ca916ebec8d06297e719368b39a2df` | accepted |  |
| ar-gul-chest-pain-06 | `cc34fca7dea02484ffc819fd3ee4e1a5b49314ed573218983dde91edbd8b7e6a` | accepted |  |
| ar-gul-chest-pain-07 | `8128d296ef50b698f4813c7ea965fe34ef39a2dec83c6a631fc6826fa6810854` | accepted |  |
| ar-gul-child-02 | `020f82784b6e75dbdf2d70fd5f22d9134f73825cbc4fa1de73f2949cdfceaa16` | accepted |  |
| ar-gul-child-03 | `04cb3e6cd08ebc21922e232785a208e98923d37f8db57c09ffc1fbc7deb83d36` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:22:08.142517+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-child-04 | `afa8f71e27e5e23974ffaf434b93038107af0ad1ebcecdf1e4c5e9333b9a2b69` | accepted |  |
| ar-gul-child-05 | `5515b60474f893f3ef5cb986521dbdacdf4a9ff663df608bfe7039bf6fcb7268` | accepted |  |
| ar-gul-child-06 | `540dd63daaa9f54091f5b96cda70ef02c4a4a568943209c08fa3d514dd3ca57b` | accepted |  |
| ar-gul-sepsis-01 | `6f7ba3c62295f38325363acd58b321527cacc4aabd2be7736c6be46ff394db55` | accepted |  |
| ar-gul-sepsis-02 | `6ce36b7af65f23bb2fc6f5cda65aab4623227b65a0ead809b6e4dcf8ffd77a03` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:22:28.649002+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-sepsis-03 | `7ee787c927654918b0e9bd0c6e3cba0b2c6e423236b2a37624238052d26865dd` | accepted |  |
| ar-gul-sepsis-04 | `4a83ece6544574a3e6529b3adf121fd21e5b73c7624e51761c5fbec55584cab0` | accepted |  |
| ar-gul-sepsis-05 | `c89bc03f8df3a80dff7cbc20f0caeecd4fbf2ff08e48e36a3008f5eff9db3e1d` | accepted |  |
| ar-gul-sepsis-06 | `efddb6a0189308721550b77e98c4954386ce118ed342676ea1702f2444ab0894` | accepted |  |
| ar-gul-sepsis-07 | `dfea8528c6568dd5e7757a5e5f6550765290adfce5dbea595592242068701e29` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:22:51.258503+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-sepsis-08 | `2fe33bdd13d43e791555c12558becc054cfedf7768042dcd24f9aed585637da8` | accepted |  |
| ar-gul-stroke-01 | `6d5e99e40a8360a97f6acc645caef11ff74faedf40a5c4989929a4827b910cbf` | accepted |  |
| ar-gul-stroke-02 | `6203ad70d0c547fe9fcfa429ca5e469efcf116d4c7161a49fa361cdb33600a90` | accepted |  |
| ar-gul-stroke-04 | `3af3374d06695863fbd6d7c2d18959355c4880a56ab45ffaeb3c7bfc27ae6dcf` | accepted |  |
| ar-gul-stroke-05 | `b526e91405e49dff5761e87a123164d92695e56f93a8e1f5a64de09d496ac3db` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:23:13.274776+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-breathing-03 | `4ef0e93eb556baa928435c469a6451ef0545a9431d9473f63f3136c693a115b2` | accepted |  |
| ar-lev-chest-pain-01 | `60d576788d5fb1090924e5d5d55c698457d6a20e0be375613bd152105fd9e665` | accepted |  |
| ar-lev-chest-pain-02 | `a0c8f7a749a230e8e507a214608f3a81ab6207c49e06c9371451cddfcae4a49f` | accepted |  |
| ar-lev-chest-pain-03 | `cb697b57d2d259fc2d7063c5e73f85d7a0219207618c2c083650afa5d6f40fa1` | accepted |  |
| ar-lev-chest-pain-04 | `0bb1da794cb9ab922fa5fe944d99c7aa2d3a42883f57f11123a4cfddb0c25a13` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:23:35.099512+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-chest-pain-05 | `91a91684a72163769e73ecc0f1c8768efc9dfb00574636cea96868fa165373ae` | accepted |  |
| ar-lev-chest-pain-06 | `e9743b64af3b62bbad026534fe3b8d9cb12ae17ffb46d801b1a2d95a8b7060ed` | accepted |  |
| ar-lev-chest-pain-07 | `f7568f208c58c3a6f5b1ce3d3e54c5e5eacca51fcaee13ca9e7396578806fb1a` | accepted |  |
| ar-lev-child-01 | `cef6f10b55af58d6d6576ad0e3435e09c13203a81465c6ea6787acde8df9a957` | accepted |  |
| ar-lev-child-02 | `4ab18ebe16fc708459903ed82d6484f3a25fdeecc253c901b6701f1f69f591b8` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:23:54.617839+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-child-03 | `d11b3effe19ce041926040889079311c76ea6c6fd3be43efaa7e93f1b60cd685` | accepted |  |
| ar-lev-child-04 | `1a8c946cba6d6359c141524a8ca7ba74f798f159b0ac87058aa9f0e050d66b56` | accepted |  |
| ar-lev-child-05 | `896d14899e5c693d032dc6d07ff5025a7c590988207831920395f62f4a6b5be5` | accepted |  |
| ar-lev-child-06 | `96f2d85cac587338fede2ba2f4a33e8ecb07fb0b73cbdf6a535fe362d628a4cb` | accepted |  |
| ar-lev-sepsis-01 | `cc8e6922e73ccb847bfce62f620edefbca1d837835588c597f50814541208e01` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:24:15.068656+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-sepsis-02 | `22eff1a57ec11fa834fd7fb3b999b5939c367349eb2f3bfbb8ae1400a7d8ccdf` | accepted |  |
| ar-lev-sepsis-03 | `48abef36d796be84dcb7f71af865966e38b981243f956cfe26ffb5e189869733` | accepted |  |
| ar-lev-sepsis-04 | `efc1ccf7a8de0005fb41b59464812539fbfb8266f5689fec9ea11982775f00e3` | accepted |  |
| ar-lev-sepsis-05 | `abc2436ec8ec119d259aaa0cab18dbd1e630e140b84f89214c1377bad62dfa11` | accepted |  |
| ar-lev-sepsis-06 | `a6555755f9184ff71e90c606bde1b31aa9d60c8a84ecdedb3872dc5e63199619` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:24:42.368271+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-sepsis-07 | `2454c0f1e8ba95a9b83f7e6d49de3a4f6a9a4797c0b6ddbbd376423e4de4f764` | accepted |  |
| ar-lev-stroke-01 | `634802030b14bad900782e672cd5383bafa7affed9cfc34ad42a746ec3d7dfb8` | rejected | Expected chief complaint specifies slurred speech; the patient reports unclear speech and difficulty speaking without establishing slurring. |
| ar-lev-stroke-02 | `e25ebaaaa752588846de5b1114d47e9d87985614ead54b06dcd2015f31156abf` | accepted |  |
| ar-lev-stroke-03 | `27af46b0206dab8531bd45d41627644f17bfac943dae4f45937fdd7a0cd2a3cc` | rejected | Expected facts omit the explicitly reported right-sided laterality of the arm weakness. |
| ar-lev-stroke-04 | `162faeae0bc767df8ed8d4d7322687beebbf3f4f8025741880d43cc7f7769de6` | rejected | Expected chief complaint specifies slurred speech; difficulty speaking and getting words out does not establish slurring. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:24:52.682682+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-stroke-05 | `6f951d0671517f89b4c2d37fa9cedcf6d6ecc1aa96c79b189caaf3f49e60cc06` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:56:52.572759+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-child-01 | `74ec3013a938fbd4ca6d2b5b7bdf9d89c63a0c12cde27759b728d0a46ffe2800` | rejected | answer 1 (chief_complaint): back-translation 'My child is sick' vs gloss 'My son is sick.': chrF 38.2 < 40; answer 4 (child_danger.non_blanching_rash): back-translation "No, he doesn't have a rash" vs gloss 'No, he has no rash.': chrF 35.3 < 40 |
| ar-lev-stroke-04 | `6399a139dd1b6c306eff9d62bee142fe0d8e8cdfebb764c09c423086d58dc0fc` | rejected | answer 4 (fast.speech_difficulty): back-translation 'Yes, speech is coming out difficult' vs gloss 'Yes, the words come out with difficulty.': chrF 36.4 < 40 |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:57:21.365307+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-routine-16 | `2ee8f46f8522b5970ee3f8d0b2d996f513140fb1eea8b2d00aa46c3136c58bcf` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:57:29.649666+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-stroke-03 | `4a12cc72a5777c53f917ec921f162adc4c98e04f208a6bf02423abf19f2b7498` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:57:37.671814+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-routine-16 | `6aadda7b6dc3eea383373d477ce1f0889e4e62ff16257f95f40c4bd883dd9f40` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:57:48.067944+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-stroke-01 | `bcaee39d29a7c02891db6b2574583f8781ea4af31ff089a33d6c291fc839d477` | rejected | Alert level 2 is invalid; FAST_STROKE is defined as critical (level 1) in the rule code.; Expected facts include 'My speech is slurred' which is not a supported extraction from the patient text. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:57:58.703762+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-stroke-03 | `2afdcf884c5fedaf4f224d8e69ac2213f44bace218111f8705a9760cb5477172` | rejected | Expected facts include 'fast.face_droop': yes, but patient text and gloss explicitly state 'No' and 'No' respectively.; Alert FAST_STROKE requires at least two FAST signs (face, arm, speech) to be yes; only arm and speech are supported. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:59:19.174856+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-child-01 | `7ae7460a0e1ed42f705f18a5c905b7f1f05a3499f9b60ec6b8b8a0ded86dc488` | rejected | Expected fact 'child_danger.hard_to_wake' is 'unknown' but patient text states 'I don't know, I'm not sure' (M2-04), which maps to 'unknown' only if the answer is 'Not sure', not 'I don't know'. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:59:29.607196+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-stroke-01 | `bcaee39d29a7c02891db6b2574583f8781ea4af31ff089a33d6c291fc839d477` | rejected | Alert level 2 is invalid; FAST_STROKE is defined as critical (level 1) in the rule code.; Expected facts include 'My speech is slurred' which is not a supported extraction from the patient text. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:59:40.926382+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-stroke-03 | `2afdcf884c5fedaf4f224d8e69ac2213f44bace218111f8705a9760cb5477172` | rejected | Expected facts include 'fast.face_droop': yes, but patient text and gloss explicitly state 'No' and 'No' respectively.; Alert FAST_STROKE requires at least two FAST signs (face, arm, speech) to be yes; only arm and speech are supported. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T07:59:51.229592+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-lev-stroke-04 | `98755035889f47f21da3eccff2d3cd2a9724c61a5bd4930166e105bf93ed5f24` | rejected | Expected alert level 2 is invalid; FAST_STROKE is critical (level 1) per rule definition.; Expected facts include 'My speech is slurred' but patient text states 'tongue is heavy'. |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.

## Automated gate decisions 2026-09-28T08:00:18.885679+00:00

| Scenario | Content SHA-256 | Decision | Reason |
|---|---|---|---|
| ar-gul-child-01 | `7ae7460a0e1ed42f705f18a5c905b7f1f05a3499f9b60ec6b8b8a0ded86dc488` | accepted |  |
| ar-lev-stroke-01 | `bcaee39d29a7c02891db6b2574583f8781ea4af31ff089a33d6c291fc839d477` | accepted |  |
| ar-lev-stroke-03 | `2afdcf884c5fedaf4f224d8e69ac2213f44bace218111f8705a9760cb5477172` | accepted |  |
| ar-lev-stroke-04 | `98755035889f47f21da3eccff2d3cd2a9724c61a5bd4930166e105bf93ed5f24` | accepted |  |

Full model identity, raw recording paths and verdicts: `review-log.jsonl`.
