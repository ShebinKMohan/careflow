# Adaptive CareFlow front door — 29 September 2026

Research prototype; all patient records and test utterances are synthetic. This evidence separates source/fixture checks from actual local-model and API execution.

## Implemented journey

Tap to begin or welcome QR → masked record lookup and birth-year verification, or new registration → actual patient versus caregiver → optional department preference → saved media/data choices → adaptive English/Arabic intake → guided adult demo measurements (paediatric nurse pathway) → own-session receipt → nurse confirmation → queue token and sourced clinician view.

The urgent shortcut precedes identity: explicit data consent creates an anonymous nurse help request, with optional symptoms and an adult/child branch. No name, age, FHIR patient or clinical severity is fabricated. Safety rules run immediately. A visit reference is available before a nurse issues a queue token.

## Verification ledger

- Kiosk: 116 unit tests passed after the final visual simplification and urgent Back navigation; focused receipt/camera/voice/proxy checks also passed. TypeScript and ESLint passed.
- Companion: 60 tests across 8 files passed; 36 affected tests subsequently passed after removing closed chief-complaint choices and refreshing the receipt immediately on queue-state changes. Counts overlap. TypeScript and ESLint passed. Actual scoped QR→lookup→birth-year verification→dependent separation passed without model calls. See [companion report](../ADAPTIVE-20260928/COMPANION.md).
- Staff: clinical provenance, absent data, caregiver separation, real model identity, stale refresh, urgency and urgent-worklist regressions passed. TypeScript and ESLint passed. See [staff report](staff/README.md).
- Backend: identity/adaptive 23 tests; urgent handoff 5 tests; own-session receipt 18 tests; model selection and safeguards 104 tests; refreshed vitals suggestions 18 tests; extraction failure/child/conversation regressions 105 tests. The latter includes four new child-opening regressions: capture the reason before the mandatory safety bank, retain an existing complaint, retain an urgent answer and alert, and preserve all 16 safety screens despite the ordinary eight-turn cap. The adult model is never called for the child branch. Counts overlap the broader suites and must not be summed as unique tests.
- Contract route and example tests: 88 passed after registering the new scoped receipt.
- Five desktop/phone EN/AR fixture browser journeys passed. Ten automated accessibility scans reported zero violations; no horizontal overflow or page errors. These scans do not constitute accessibility certification. See [UI report](ui/README.md).
- Camera verification used actual Chromium getUserMedia with a synthetic video device, proving explicit activation, manual stop and navigation cleanup. It did not capture a person or inspect physical Mac camera permissions.
- Live urgent-help browser journey passed: anonymous request, actual chest-pain safety-rule code, nurse worklist reference, simulated vitals, nurse-confirmed urgency and a real queue token. Request save measured 177 ms in that run. See `live/` for case evidence.

## Actual model and performance boundaries

The active API diagnostic identifies `laya-ktas` / `laya-ktas-ebf31b5e-s42`, replacing the previously selected HGB baseline. The actual selected loader was tested on this Mac: approximately 9.3 s load and 1.1–1.5 s prediction with three fact-linked explanations. This is a local inference measurement, not clinical validation, a percentile study, or an ONNX readiness claim. See [selected-loader measurement](../2026-09-29-laya-selected-loader.json).

The first live PTT attempt after a cold restart exposed a Qwen extraction timeout: microphone release and Whisper transcription worked, but extraction spent approximately 19.3 s in initial prompt prefill against the existing 20 s deadline. The failed cold result is retained in `voice-cold-first-run.json`. A startup-only warmup now verifies both English and Arabic extraction with fixed synthetic inputs before the API accepts requests. The patient timeout remains unchanged. An incomplete extraction also preserves an exact, sourced complaint rather than repeatedly asking for it; it does not invent categories or clinical findings.

A fully cached 4B alternative was tested and rejected as the default: it improved some timings but mistranslated the Arabic word for wrist as elbow and omitted that sample's chief complaint. The readiness check failed rather than allowing that model to accept patient requests. Its raw synthetic probe is retained in `model-4b-probe.json`. The durable local default remains pinned Qwen3.5-9B; the smaller model requires explicit experimental selection and reports its actual identity. These few probes do not establish the larger model's general Arabic or clinical accuracy.

The final post-warmup voice and complete adaptive model journeys are recorded separately by the live validation runs. Do not substitute fixture results or a healthy HTTP endpoint for those results.

The compact schema experiment is now opt-in (`CAREFLOW_EXPERIMENTAL_COMPACT_EXTRACTION=1`), not the default: a genuine English voice case produced an invalid null-valued age fact from the registration boilerplate. The full canonical schema succeeded on the same statement. A subsequent presentation-only fix removes verified, server-generated registration metadata from model input while retaining the original transcript, typed facts, labels, and source-span verification. Its 119 focused checks passed. The local provider now consumes streamed responses and closes them on cancellation; 23 adapter/structured tests passed. Its live cancellation proof was not run after the owner paused voice work.

Voice development and further voice benchmarking were explicitly paused by the owner. Prior canonical English synthetic-audio passes reached the first spoken reply in 13.0–14.2 s. An Arabic synthetic-audio pass reached the transcript in 2.70 s, the next Arabic question a further 13.21 s later, and first MMS speech 16.20 s after release. These are bounded local demonstrations, not a latency guarantee; the experimental English failure is retained. No physical microphone audio was captured.

The final patient interface removes duplicate explanations and inactive media panels. The actual question is the single heading; the microphone and answer box are primary, example chips explicitly say “Example” and focus the editable field, and camera/phone handoff are secondary controls. Five EN/AR viewport cases and all ten accessibility scans passed again after this change.

## Remaining capability boundaries

- The camera is an optional local preview. There is no camera-derived distress assessment or cough classifier in this change.
- Mac Touch ID is not a pulse oximeter. Vitals in these demonstrations are explicitly simulated and source labelled.
- Model suggestions remain provisional; deterministic safety alerts retain priority and a nurse confirms every final level. Codes such as FAST_STROKE identify safety rules, not hospital emergency announcements or diagnostic/billing codes.
- Phone capture requires a secure origin and reachable local speech service. The local HTTP phone demo preserves typed input and explicitly explains unavailable microphone capture.
- Record lookup is gated to the synthetic demo hospital; mobile plus birth year is not presented as production authentication.
- Arabic presentation and synthetic checks are not native-speaker validation or a clinical accuracy claim.
- Eight historical FHIR writeback failures were already present before this work. New live cases check their own writeback rather than deleting historical failures.

This ledger records local prototype verification. Deployment status is recorded separately in the release notes. Local browser traces/reports and live welcome QR images containing signed entry capabilities are ignored; screenshots and reports describe their own proof boundaries.
