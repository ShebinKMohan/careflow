Latest report: [20260927-095128.md](20260927-095128.md) · [JSON](20260927-095128.json)

# CareFlow extraction_en evaluation

Research prototype. Synthetic data only. Not for clinical use.

Scenario review is automated, not performed by clinicians or native speakers.
Expected urgency levels are synthetic scenario labels; their agreement is not clinical triage accuracy.
A completed run describes execution, not a clinical pass or acceptable model quality.

**Run status:** completed

## Provenance

| Field | Recorded value |
| --- | --- |
| case_count | 50 |
| completed_count | 50 |
| dataset_sha256 | b8e3c26807e65a4d634c29bbb4fa783e78f5880eb22399c1bcd019e49d4f0bc9 |
| git_sha | 989981b5c265a70ac2fb466459694ca9b6dad968 |
| include_unreviewed | false |
| latency_source | ["measured_live"] |
| local_run_evidence | eval/reports/local-runs/20260927-094321-489032-baseline |
| logical_model | qwen3.5-9b-4bit |
| metrics_comparable | true |
| missing_ids | [] |
| mode | record |
| model_endpoint_checks | [{"base_url": "http://127.0.0.1:8081/v1", "checked_at": "2026-09-27T09:43:32.262721+00:00", "served_model": "/Users/shebin_mohan_k/.cache/huggingface/hub/models--mlx-community--Qwen3.5-9B-MLX-4bit/snapshots/938d8919941c6e7efd3c7150eff7fe9d12afa631", "server_model_ids": ["/Users/shebin_mohan_k/.cache/huggingface/hub/models--mlx-community--Qwen3.5-9B-MLX-4bit/snapshots/938d8919941c6e7efd3c7150eff7fe9d12afa631", "mlx-community/SmolLM2-135M-Instruct-8bit"]}, {"base_url": "http://127.0.0.1:8081/v1", "checked_at": "2026-09-27T09:51:28.223849+00:00", "served_model": "/Users/shebin_mohan_k/.cache/huggingface/hub/models--mlx-community--Qwen3.5-9B-MLX-4bit/snapshots/938d8919941c6e7efd3c7150eff7fe9d12afa631", "server_model_ids": ["/Users/shebin_mohan_k/.cache/huggingface/hub/models--mlx-community--Qwen3.5-9B-MLX-4bit/snapshots/938d8919941c6e7efd3c7150eff7fe9d12afa631", "mlx-community/SmolLM2-135M-Instruct-8bit"]}] |
| model_receipt | {"base_url": "http://127.0.0.1:8081/v1", "identity_limit": "Launch-log attribution and endpoint observations, not weight attestation", "launch_evidence": "with_llm: starting mlx_lm.server (mlx-community/Qwen3.5-9B-MLX-4bit@938d8919941c6e7efd3c7150eff7fe9d12afa631) on http://127.0.0.1:8081; log /var/folders/wz/1j18wyv50vb_g1hlhd5zz_yw0000gn/T//careflow-mlx-lm-server.log", "launch_log_sha256": "dcc03ac93963e3fc3dc97a05960c0c8046af10225a2b6a37b97b34710937c7d9", "model_id": "mlx-community/Qwen3.5-9B-MLX-4bit", "observed_at": "2026-09-27T09:43:21.478636+00:00", "revision": "938d8919941c6e7efd3c7150eff7fe9d12afa631", "served_model": "/Users/shebin_mohan_k/.cache/huggingface/hub/models--mlx-community--Qwen3.5-9B-MLX-4bit/snapshots/938d8919941c6e7efd3c7150eff7fe9d12afa631", "server_model_ids": ["/Users/shebin_mohan_k/.cache/huggingface/hub/models--mlx-community--Qwen3.5-9B-MLX-4bit/snapshots/938d8919941c6e7efd3c7150eff7fe9d12afa631", "mlx-community/SmolLM2-135M-Instruct-8bit"], "source": "Fresh scripts/with_llm.sh launch from the pinned Hugging Face snapshot"} |
| normalization_version | field-values-v1 |
| prompt_sha256 | 48ad75cc29e5218bf9890b5368e27b70753a251c0f5b86931e7fa743a21d6583 |
| prompt_version | extraction_v2 |
| provider | local |
| recording_provenance | {"git_sha": "989981b5c265a70ac2fb466459694ca9b6dad968", "source_sha256": "14f9d06f89630764aedd6d67613bb5192e8b7fa31d1aec79c764fcdc5dfb9542", "worktree_dirty": true} |
| replay_exact | n/a |
| rules_validation | false |
| ruleset_version | 2026.3.0 |
| source_sha256 | 14f9d06f89630764aedd6d67613bb5192e8b7fa31d1aec79c764fcdc5dfb9542 |
| synthetic_only | true |
| timeout_seconds | 20.0 |
| wall_ms | 476170.89204199146 |
| warmup | {"duration_ms": 10560.34979200922, "included_in_case_latencies": false, "output_sha256": "669cee2aff9f78fbd03c6bc83409c30a4da6c144d6885f0ca3de9704b4362de6", "profile": "production extraction prompt and schema, empty transcript, no case input", "raw_text": "{\\n  \\"facts\\": []\\n}"} |
| worktree_dirty | true |

## Summary

| Metric | Observed value |
| --- | --- |
| alert recall | 0.96 |
| dropped fact rate | 0.06 |
| extraction field f1 | 0.7417475728155339 |
| extraction incomplete rate | 0.02 |
| false alert rate | 0.0 |
| field f1 | 0.7417475728155339 |
| field precision | 0.8025210084033614 |
| field recall | 0.6895306859205776 |
| latency p50 ms | 9113.418707973324 |
| latency p95 ms | 12284.610874950886 |

## alert recall.details.per rule

| key | benign_false_alert_cases | benign_false_alert_rate | detected | expected | missed | recall | unexpected |
| --- | --- | --- | --- | --- | --- | --- | --- |
| CHEST_PAIN_ECG_10MIN | 0 | 0.0 | 6 | 6 | 0 | 1.0 | 0 |
| CHILD_HARD_TO_WAKE | 0 | 0.0 | 1 | 1 | 0 | 1.0 | 0 |
| FAST_STROKE | 0 | 0.0 | 5 | 5 | 0 | 1.0 | 0 |
| NG143_AMBER_FLAG | 0 | 0.0 | 2 | 2 | 0 | 1.0 | 0 |
| NG143_RED_FLAG | 0 | 0.0 | 3 | 4 | 1 | 0.75 | 0 |
| SEPSIS_NEWS2 | 0 | 0.0 | 7 | 7 | 0 | 1.0 | 0 |

## extraction field f1.details.per key

| key | f1 | fn | fp | precision | recall | tp |
| --- | --- | --- | --- | --- | --- | --- |
| allergies_confirm | 1.0 | 0 | 0 | 1.0 | 1.0 | 13 |
| chest_pain.onset | 0.0 | 4 | 4 | 0.0 | 0.0 | 0 |
| chest_pain.present | 0.9230769230769231 | 1 | 1 | 0.9230769230769231 | 0.9230769230769231 | 12 |
| chest_pain.radiates | 0.7692307692307693 | 2 | 1 | 0.8333333333333334 | 0.7142857142857143 | 5 |
| chest_pain.short_of_breath | 0.9523809523809523 | 1 | 0 | 1.0 | 0.9090909090909091 | 10 |
| chest_pain.sweating | 1.0 | 0 | 0 | 1.0 | 1.0 | 3 |
| chief_complaint | 0.36363636363636365 | 32 | 31 | 0.3673469387755102 | 0.36 | 18 |
| child_amber.poor_feeding | 0.8888888888888888 | 1 | 0 | 1.0 | 0.8 | 4 |
| child_amber.reduced_urine_output | 1.0 | 0 | 0 | 1.0 | 1.0 | 3 |
| child_danger.grunting | 0.8888888888888888 | 1 | 0 | 1.0 | 0.8 | 4 |
| child_danger.hard_to_wake | 0.9090909090909091 | 1 | 0 | 1.0 | 0.8333333333333334 | 5 |
| child_danger.non_blanching_rash | 1.0 | 0 | 0 | 1.0 | 1.0 | 2 |
| child_danger.pale_mottled_ashen_or_blue | 0.0 | 1 | 0 | n/a | 0.0 | 0 |
| fast.arm_weakness | 1.0 | 0 | 0 | 1.0 | 1.0 | 6 |
| fast.face_droop | 1.0 | 0 | 0 | 1.0 | 1.0 | 6 |
| fast.last_known_well | 0.3333333333333333 | 4 | 4 | 0.3333333333333333 | 0.3333333333333333 | 2 |
| fast.speech_difficulty | 1.0 | 0 | 0 | 1.0 | 1.0 | 6 |
| infection.fever_reported | 1.0 | 0 | 0 | 1.0 | 1.0 | 25 |
| infection.rigors | 0.9523809523809523 | 1 | 0 | 1.0 | 0.9090909090909091 | 10 |
| pain_location | 0.7368421052631579 | 5 | 0 | 1.0 | 0.5833333333333334 | 7 |
| pain_present | 0.0 | 13 | 0 | n/a | 0.0 | 0 |
| pain_score | 1.0 | 0 | 0 | 1.0 | 1.0 | 13 |
| severity | 1.0 | 0 | 0 | 1.0 | 1.0 | 19 |
| symptom_onset_at | 0.75 | 6 | 6 | 0.75 | 0.75 | 18 |
| symptoms | 0.0 | 13 | 0 | n/a | 0.0 | 0 |

### alert recall details

| Detail | Recorded value |
| --- | --- |
| benign_cases | 26 |
| case_count | 50 |
| detected_alerts | 24 |
| expected_alerts | 25 |
| false_alert_cases | 0 |
| false_alert_rate | 0.0 |
| missed_alerts | 1 |
| recall | 0.96 |
| unexpected_alerts | 0 |

### dropped fact rate details

| Detail | Recorded value |
| --- | --- |
| candidate_rejection_rate | n/a |
| case_count | 50 |
| cases_with_dropped_facts | 3 |
| definition | fraction_of_cases_with_at_least_one_rejected_candidate |
| denominator | all_cases |
| dropped_total | 3 |

### extraction field f1 details

| Detail | Recorded value |
| --- | --- |
| case_count | 50 |
| expected_facts | 277 |
| f1 | 0.7417475728155339 |
| fn | 86 |
| fp | 47 |
| normalization_version | field-values-v1 |
| precision | 0.8025210084033614 |
| predicted_facts | 238 |
| recall | 0.6895306859205776 |
| tp | 191 |

### extraction incomplete rate details

| Detail | Recorded value |
| --- | --- |
| case_count | 50 |
| denominator | all_cases |
| incomplete_cases | 1 |

### false alert rate details

| Detail | Recorded value |
| --- | --- |
| benign_cases | 26 |
| case_count | 50 |
| detected_alerts | 24 |
| expected_alerts | 25 |
| false_alert_cases | 0 |
| false_alert_rate | 0.0 |
| missed_alerts | 1 |
| recall | 0.96 |
| unexpected_alerts | 0 |

### field f1 details

| Detail | Recorded value |
| --- | --- |
| case_count | 50 |
| expected_facts | 277 |
| f1 | 0.7417475728155339 |
| fn | 86 |
| fp | 47 |
| normalization_version | field-values-v1 |
| precision | 0.8025210084033614 |
| predicted_facts | 238 |
| recall | 0.6895306859205776 |
| tp | 191 |

### field precision details

| Detail | Recorded value |
| --- | --- |
| case_count | 50 |
| expected_facts | 277 |
| f1 | 0.7417475728155339 |
| fn | 86 |
| fp | 47 |
| normalization_version | field-values-v1 |
| precision | 0.8025210084033614 |
| predicted_facts | 238 |
| recall | 0.6895306859205776 |
| tp | 191 |

### field recall details

| Detail | Recorded value |
| --- | --- |
| case_count | 50 |
| expected_facts | 277 |
| f1 | 0.7417475728155339 |
| fn | 86 |
| fp | 47 |
| normalization_version | field-values-v1 |
| precision | 0.8025210084033614 |
| predicted_facts | 238 |
| recall | 0.6895306859205776 |
| tp | 191 |

### latency p50 ms details

| Detail | Recorded value |
| --- | --- |
| case_count | 50 |
| method | nearest_rank |
| missing_count | 0 |
| percentile | 50 |
| sample_count | 50 |
| units | ms |

### latency p95 ms details

| Detail | Recorded value |
| --- | --- |
| case_count | 50 |
| method | nearest_rank |
| missing_count | 0 |
| percentile | 95 |
| sample_count | 50 |
| units | ms |

## Failing or incomplete scenario IDs

- breathing-low-oxygen-no-infection
- breathing-mild-focused-screen
- breathing-moderate-no-infection
- breathing-persistent-no-chest-pain
- chest-pain-localised
- chest-pain-no-breathlessness
- chest-pain-radiating-arm
- chest-pain-screen-negative
- chest-pain-screen-uncertain
- chest-pain-sweating
- chest-pain-unsure-radiation
- chest-pain-with-jaw-radiation
- child-feeding-and-urine-amber
- child-grunting-and-poor-feeding
- child-hard-to-wake-under-five
- child-non-blanching-rash
- child-older-hard-to-wake
- child-parent-unsure-waking
- routine-01-wrist-ache
- routine-02-ankle-soreness
- routine-03-knee-ache
- routine-04-shoulder-stiffness
- routine-05-elbow-tenderness
- routine-06-heel-discomfort
- routine-07-finger-ache
- routine-08-dry-skin
- routine-09-scalp-itch
- routine-10-flaky-skin
- routine-11-ear-itch
- routine-12-blocked-ear
- routine-13-bloating
- routine-14-constipation
- routine-15-queasiness
- routine-16-toenail-irritation
- routine-17-foot-blister
- routine-18-dry-lips
- sepsis-kiosk-below-threshold
- sepsis-kiosk-high-fever
- sepsis-kiosk-threshold-five
- sepsis-low-measured-temperature
- sepsis-measured-fever-denied
- sepsis-nurse-completes-high-score
- sepsis-nurse-confirms-threshold
- sepsis-rigors-with-normal-temperature
- stroke-arm-positive
- stroke-face-positive
- stroke-last-known-well-unknown
- stroke-multiple-signs
- stroke-screen-unsure
- stroke-speech-positive

## Scenario outcomes

| Scenario | Execution status | Rule expectations matched | Error / reason |
| --- | --- | --- | --- |
| breathing-low-oxygen-no-infection | completed | true | n/a |
| breathing-mild-focused-screen | completed | true | n/a |
| breathing-moderate-no-infection | completed | true | n/a |
| breathing-persistent-no-chest-pain | completed | true | n/a |
| chest-pain-localised | completed | true | n/a |
| chest-pain-no-breathlessness | completed | true | n/a |
| chest-pain-radiating-arm | completed | true | n/a |
| chest-pain-screen-negative | completed | true | n/a |
| chest-pain-screen-uncertain | completed | true | n/a |
| chest-pain-sweating | completed | true | n/a |
| chest-pain-unsure-radiation | completed | true | n/a |
| chest-pain-with-jaw-radiation | completed | true | n/a |
| child-feeding-and-urine-amber | completed | true | n/a |
| child-grunting-and-poor-feeding | completed | true | n/a |
| child-hard-to-wake-under-five | completed | true | n/a |
| child-non-blanching-rash | completed | true | n/a |
| child-older-hard-to-wake | completed | true | n/a |
| child-parent-unsure-waking | completed | false | n/a |
| routine-01-wrist-ache | completed | true | n/a |
| routine-02-ankle-soreness | completed | true | n/a |
| routine-03-knee-ache | completed | true | n/a |
| routine-04-shoulder-stiffness | completed | true | n/a |
| routine-05-elbow-tenderness | completed | true | n/a |
| routine-06-heel-discomfort | completed | true | n/a |
| routine-07-finger-ache | completed | true | n/a |
| routine-08-dry-skin | completed | true | n/a |
| routine-09-scalp-itch | completed | true | n/a |
| routine-10-flaky-skin | completed | true | n/a |
| routine-11-ear-itch | completed | true | n/a |
| routine-12-blocked-ear | completed | true | n/a |
| routine-13-bloating | completed | true | n/a |
| routine-14-constipation | completed | true | n/a |
| routine-15-queasiness | completed | true | n/a |
| routine-16-toenail-irritation | completed | true | n/a |
| routine-17-foot-blister | completed | true | n/a |
| routine-18-dry-lips | completed | true | n/a |
| sepsis-kiosk-below-threshold | completed | true | n/a |
| sepsis-kiosk-high-fever | completed | true | n/a |
| sepsis-kiosk-threshold-five | completed | true | n/a |
| sepsis-low-measured-temperature | completed | true | n/a |
| sepsis-measured-fever-denied | completed | true | n/a |
| sepsis-nurse-completes-high-score | completed | true | n/a |
| sepsis-nurse-confirms-threshold | completed | true | n/a |
| sepsis-rigors-with-normal-temperature | completed | true | n/a |
| stroke-arm-positive | completed | true | n/a |
| stroke-face-positive | completed | true | n/a |
| stroke-last-known-well-unknown | completed | true | n/a |
| stroke-multiple-signs | completed | true | n/a |
| stroke-screen-unsure | completed | true | n/a |
| stroke-speech-positive | completed | true | n/a |
