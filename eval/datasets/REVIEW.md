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
