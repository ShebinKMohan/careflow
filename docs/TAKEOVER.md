# CareFlow takeover working guide

Date: 27 September 2026. This is an implementation and verification plan, not a clinical release claim.

## Checkout and evidence boundary

- Takeover branch: `codex/careflow-takeover`.
- Starting commit: `adb8908186b0ef5968ef88950356ec6c8d562436`.
- Isolated checkout: `/Users/shebin_mohan_k/.codex/worktrees/careflow-takeover/careflow`.
- The primary checkout at `/Users/shebin_mohan_k/CODES/CursorFiles/careflow` was left intact on dirty branch `she-317-laya-onnx-kiosk`, observed at `30bd9bb2a778a70bd7a02060d650609348add930`. Its uncommitted ONNX, contracts, API and kiosk changes are not part of this base.
- **The Linear connector stays disconnected for shared-account privacy.** The user authorized visual inspection of the local Linear app; the coordinating agent verified the CareFlow board on 27 September: **62 issues = 24 Backlog, 0 Todo, 1 In Progress, 14 In Review, 23 Done**. These are observed board states, not statuses inferred from code.
- In Progress: `SHE-317 [M3-02]`; its visible blockers are `M1-19` and `M3-01` (In Review). Earliest eligible Backlog: **`SHE-314 [M1-18]` evaluation harness**, with no remaining “Blocked by” entries. Also visible: `SHE-310 [M2-11]` RAG evaluation and `SHE-311 [M2-12]` seven journeys. Other issue details/dependencies still need inspection.
- This audit inspected source, configuration, ADRs and checked-in reports. Historical commands, smoke checks and metrics quoted below were reported by earlier work; this document does not claim to have rerun them.
- First implementation on this branch: **phone intake refresh recovery — implemented and locally verified**. It includes revision-protected answer retries, terminal-session reconnection and called-room recovery. The whole QR handover milestone still needs its real-stack acceptance checks; see [evidence](evidence/TAKEOVER-phone-recovery.md).

## Product mission and scope

CareFlow collects a patient's story once, privately, brings danger signs to a nurse's attention, supports the nurse's urgency decision, manages the wait, and gives the doctor a briefing whose facts have traceable sources. The problems are repeated forms, private details spoken in a lobby, language barriers, missed deterioration while waiting, and fragmented clinical handover.

The documented delivery scope is a local research prototype: synthetic patients, a simulated hospital and vitals device, and mandatory nurse decisions. Current instructions request realistic working behavior and actual content. Implement real application logic and integrations; keep synthetic patient fixtures clearly labelled. Authentic clinic handbook content, real hardware and hospital access require identified sources and integration details. Never manufacture those inputs or silently present fixtures as operational data.

Source of scope: [README](../README.md), [owner decisions](CONVENTIONS.md), [plan](PLAN.md), [runbook](AGENT_RUNBOOK.md). The owner decisions override older plan sections. The pitch and architecture drawings describe the intended experience; they do not prove deployment or completion.

| Person | Complete experience |
|---|---|
| Adult patient | Choose English/Arabic, consent, identify privately, answer by text/tap/voice, receive token and room. |
| Parent/carer | Answer for a child using child-specific danger questions; adult urgency inference must not substitute for the nurse. |
| Patient on phone | Continue the same intake after QR handover, recover after refresh, follow the queue, ask practical clinic questions, report feeling worse. |
| Nurse | See alerts and missing information, add measurements, confirm or override urgency with a reason, call patients, handle deterioration and no-shows. |
| Doctor | Read the confirmed decision and briefing, inspect each fact's provenance, and find the corresponding hospital record. |
| Clinic administrator | Inspect service/provider health, clinic configuration and later audit, model and operational reports. |

Core flow: consent → identification → FHIR history → intake facts → safety rules/alerts → urgency suggestion → nurse decision → queue/token → FHIR write-back → doctor briefing. The phone remains useful during the wait.

## Implementation and proof matrix at the takeover base

“Source present” means code was found. “Reported evidence” means a repository document records an earlier result. Neither establishes that the complete stack works at the takeover commit.

| Area | Implementation inspected | Proof still required |
|---|---|---|
| Contracts and foundation | OpenAPI/generated clients, FastAPI, auth, clinic RLS, migrations, Docker/launch scripts. M0 evidence exists. | Fresh startup, current contract conformance, role isolation and clinic isolation. |
| Rules and intake | Deterministic rules, LangGraph, structured extraction, checklist conversation and persistent checkpoints. M1-01/02/05/06/07 evidence exists. | Live extraction through the graph; alerts before suggestions; failure, timeout and restart behavior. |
| FHIR and briefing | Read adapters, transaction write-back and attributed Composition/briefing builders. M1-08/09/16 evidence exists. | Current HAPI round-trip, idempotency, unavailable-EHR behavior and verifiable briefing sources. |
| Queue and events | Persistent tokens, rooms, grace timer, outbox/streams and WebSocket delivery. M1-10/11 evidence exists. | Real staff/board/phone propagation, reconnect/retry behavior and no-show transitions. |
| Runtime and staff | Patient/nurse routes, nurse screen, board, doctor and admin screens are present. ADR-031 describes M1-12 core integration. | M1-12 contract/flow tests; whole-stack journeys. No M1-12 evidence file at this base. |
| Kiosk and voice | Check-in, consent, intake, simulated vitals, local voice channel and device setup. Earlier M1-13/14/15 evidence exists. | Real-model voice path, media consent, error recovery, latency and no audio persistence. |
| Arabic and children | RTL/messages, Arabic extraction/speech selection, child questions and child safety branches. | ADRs explicitly defer test phases; Arabic remains machine-checked only, without a native-speaker accuracy claim. |
| Consent and ID tap | Consent storage/enforcement and synthetic card-reader integration. | ADR-036's negative consent, no-media, ID matching and privacy checks. No real ID service exists. |
| QR and waiting room | Signed handover, phone-channel tokens, kiosk revocation, phone intake/status and feels-worse paths. | Refresh recovery, handover security, reconnect and two-context browser journeys (ADR-037/038). Phone voice is not implemented. |
| Clinic questions | Fictional bilingual handbook, ingestion, retrieval, refusal/citation agent and intake side-question routing. | Handbook review, labelled retrieval/answer/refusal evaluations and substantive tests. `services/rag/tests/` contains only an import test at the base. |
| Urgency models | KTAS baseline and Laya training/evaluation code; aggregate result reports. | Fair baseline selection, missing-vital behavior and model registry/ONNX parity. Dirty primary-checkout ONNX work must be accounted for separately. |
| Later roadmap | M3 on-device features and M4–M6 integration/governance/proof requirements are described. | No Tauri shell, cough/pose implementation, forecast service or telemetry package found at this base. HL7/OIE, SMART, CDS Hooks, subscriptions and external signed-webhook delivery require implementation verification/completion. |

Checked-in evidence covers M0-01…M0-08 and M1-01…M1-11/M1-13…M1-16. The root `e2e/` directory and the planned comprehensive evaluation datasets are absent. Existing application smoke tests and prerecorded provider fixtures remain useful, but cannot satisfy the real-backend gates below.

## Priority defect and proof inventory

| Priority | Finding and consequence | Evidence / disposition |
|---|---|---|
| P1, fixed here | Refresh after QR claim loses the in-memory intake result after the single-use fragment has been cleared, despite a stored phone token/session. The patient can lose the continuation screen. | Server checkpoint recovery and atomic answer revisions implemented; regression, contract and browser checks recorded in `evidence/TAKEOVER-phone-recovery.md`. |
| P1 | Nurse detail loads only on mount/session change and hides its rule alerts after confirmation. Later deterioration can be missed on the screen the nurse is using. | Direct source inspection: `apps/staff/src/components/nurse/NurseSession.tsx`; fix live refresh and visibility independent of confirmation. |
| P1 | Queue call/arrived/no-show/close APIs have no operator controls in the staff app. A browser-only clinic workflow stops after token assignment. | Team audit and source search: no lifecycle API callers in `apps/staff/src`; build operator UI and its authorization/state tests. |
| P1 | Failed FHIR writes can be acknowledged as processed because the consumer logs the returned failure without raising. | Direct source inspection: `intake/consumers.py` returns normally; `events/bus.py` then marks processed and ACKs. Preserve retryable failures and prove recovery. |
| P1 | A Redis connection failure in the read/reclaim loop can terminate the consumer background task. | Direct source inspection: `events/bus.py` catches handler failures but not transport errors around the loop. Add bounded reconnection and health evidence. |
| P1 | Tests were observed publishing to the development event stream; an existing DLQ worktree patch is unfinished. | Team runtime audit; not reproduced by this document. Isolate test stream/database identities before more integration tests; account for existing patch first. |
| P1 | The RAG final answer gate accepts valid citation IDs without checking whether the answer itself contains unsafe medical output. | Direct source inspection: `services/rag/src/careflow_rag/agent.py::check_citations`. Add an independent patient-output safety gate and adversarial tests. |
| P1 | A physical phone scanning the default QR is sent to its own localhost, not the host machine. | Direct source inspection: `ContinueOnPhone.tsx` defaults to `http://localhost:3002`. Configure and test reachable companion/API addresses and the actual device path. |
| P1 | Both kiosk and phone patient tokens expire after 30 minutes, with no renewal flow found; longer waits lose authenticated status and deterioration reporting. | `routers/sessions.py::patient_token`, team route audit. Implement bounded session renewal/recovery and expiry tests without extending revoked handovers. |
| P1 | Many core features were built ahead of their test phases; the app can appear complete without integrated safety/security proof. | ADR-031, ADR-033…ADR-042 explicitly leave checks pending. Build current real-stack journey coverage. |
| P1 | RAG mode and refusal/unknown thresholds are provisional; handbook review and evaluation are pending. | ADR-039/040, `services/rag/handbook/REVIEW.md`, `config/README.md`. Validate before claiming grounded answers. |
| P1 | Urgency reports show substantial under-triage and do not establish deployment safety. | Reported HGB 14/37 (37.8%), LR 9/37 (24.3%), Laya full 12/37 (32.4%), Laya kiosk-partial 14/37 (37.8%). Preserve nurse control and compare all candidates; these are model-only results. |
| P1 | Dirty M3-02 work has reported child out-of-scope and stale-facts-hash races in the kiosk inference path. | Team audit of preserved primary checkout, outside takeover base; review/fix before integrating or declaring ONNX ready. |
| P2 | `make up` does not start the configured local extractor unless `LOCAL_LLM=1` is supplied. Healthy web services can still have unavailable extraction. | Direct source inspection: `scripts/up.sh`. Readiness must reflect live task availability; startup evidence must name the actual provider. |
| P2 | Current docs mix historical stubs, present code and future features. Pitch claims include instant records, printed tickets, stroke-team contact and unconditional residency. | `apps/README.md`, `docs/pitch/index.html`, `docs/architecture.html`. Rewrite claims against verified runtime behavior; physical printing and external contact need their own evidence. |
| P2 | The architecture still mentions SILMA and free Kaggle GPU; older plan sections mention excluded datasets/hosting. | Current defaults are local Qwen/Whisper/Kokoro/MMS, with zero paid services and no cloud GPU. Follow CONVENTIONS. |

The historical Laya report also records worse test ECE after temperature scaling, so its calibration acceptance criterion was not met. The uncommitted primary checkout's ONNX registry card reports agreement on 50 synthetic golden states and leaves test-split parity pending. Neither result has been reproduced for this takeover.

Do not confuse the rules engine's execution time with patient-input-to-nurse-alert latency: free-text input can require model extraction before the rules receive its facts. Record timestamps across the complete path.

## Dependencies and realistic boundaries

- Local stack: Docker, Postgres/pgvector/pg_textsearch, Valkey, HAPI FHIR, Python/uv, Node/pnpm, and pinned model artifacts. Budget: 18 GB machine, Docker ≤6 GB, one local LLM; no paid services or account creation.
- Default live language model: local Qwen3.5-9B MLX; optional configured APIs must be identified explicitly in evidence. Replay fixtures are test infrastructure, not live-model proof.
- KTAS: public research dataset of 1,267 adults from two Korean EDs. Raw rows stay out of Git; results do not generalize automatically to UK/UAE populations or children. Missing-data fixture models must stay labelled `fixture-*`.
- Speech: Whisper and Kokoro locally; Arabic MMS fallback is documented as non-commercial. Cohere Arabic is gated and not downloaded by default. A commercial product needs a suitable Arabic speech licence and validation.
- Clinic content: handbook is fictional, including insurers. Real content needs a named clinic and attributable, approved source documents; this is separate from synthetic patient testing.
- Hospital/device/ID: HAPI, vitals and ID tap are simulations. Real hospital credentials, mappings, device specifications and deployment environment are not supplied by this repository.
- Arabic: no native reviewer or real-world accuracy claim. TTS-generated evaluation speech is not representative patient speech.
- Clinical operation: the prototype label, rules, mandatory nurse confirmation, attributable facts and patient medical-answer refusal remain part of every gate.

## Completion sequence

1. Preserve active work and keep implementation isolated. Inventory primary-checkout changes and any existing event/DLQ checkout before integrating overlapping work.
2. Fix phone refresh recovery; test successful resume, expired/invalid session, unavailable backend, completed intake and English/Arabic continuity. Record commands/results before marking it verified.
3. Take the earliest eligible issue, **SHE-314 [M1-18]**, and establish isolated evaluation/test resources before runtime execution. Build its harness and scenarios as specified below; use its evidence to expose and clear deferred review work.
4. Establish repeatable startup on the exact takeover commit and resolve the runtime/security/event defects. Record service identity, model versions, migrations, skips and failures; prevent port collisions. Prioritize consent, nurse authority, clinic/session isolation, handover, deterioration delivery and queue controls.
5. Finish postponed M1/M2 verification in dependency order, then implement the seven real-backend journeys below, including provider/EHR/queue/WebSocket failures and restart/reconnect recovery. Keep deterministic lower-level tests alongside live-path checks.
6. Continue inspecting live Linear issue details and reconcile existing branches; take the lowest genuinely unblocked issue under the runbook. Do not mark the 14 In Review issues complete simply because their implementation was merged.
7. Complete and measure M3 on-device work, comparing LR/HGB/Laya and actual served artifacts on consistent splits. Publish missed targets and uncertainty; never weaken safety checks to obtain green results.
8. Complete required M4 integrations and M5 governance/operations in dependency order, then M6 evidence/docs. Optional external prerequisites may be recorded as unavailable; a required product journey cannot pass with `not_run`.
9. Produce a final result tied to commit/artifacts: reproducible startup, seven passing journeys, current reports, remaining external limits and accurate screenshots/demo. Public publication is outside this takeover change.

### Next backlog issue: SHE-314 [M1-18]

The coordinating agent read this issue in the live local Linear UI. Required work: an `eval` workspace package with `careflow-eval` argparse commands `run`, `generate`, `validate`, `list`; JSON Schema plus a reviewed-only loader and dataset hash; a registry with `rules` and `extraction_en` suites. Metrics include field precision/recall/F1 (numeric tolerance ±0.1; an incorrect value counts as both FP and FN), alert recall/false alerts, incomplete/dropped facts, and p50/p95 timings.

The 50 English scenarios must contain 8 chest-pain, 6 stroke, 8 sepsis, 6 child, 4 breathing and 18 routine cases, with zero rule inconsistencies. Review requires schema/rule checks, a PHI denylist and a different model from the writer as reviewer. Run a live local-Qwen baseline, record provider responses, and prove replay reproduces that exact baseline. This issue blocks M2-03, M2-11 and M5-06. Do not replace its live baseline with a fabricated or fixture-only result.

## Seven real-backend journey gates

Run these against the actual API, Postgres, event delivery, HAPI and browser apps with synthetic identities from `infra/seed/personas.yaml`. Use the real configured local extractor for the live-path run and real speech engines for at least one consenting voice variant. Do not substitute Prism, fixed API responses or prerecorded provider output for this evidence. Simulated vitals remain explicitly labelled.

Every journey records commit, configuration/model versions, timestamped session/trace identifiers, commands, screenshots and assertions. Verify source facts, event order, nurse decision and persisted result—not just visible success text. Refreshing or retrying must not duplicate tokens, decisions or FHIR resources.

1. **Chest pain — `chest-pain` / `100001`.** Consent and check in; report the persona's complaint. Assert `CHEST_PAIN_ECG_10MIN` reaches the nurse before any urgency suggestion, with arrival-based deadline. Before nurse confirmation there is no final level or queue token. Confirm/override with required reason; verify token, attributed briefing, HAPI Encounter/observations/Flag/ECG Task and idempotent retry. Measure full input-to-alert latency; publish p95 against the ≤1,000 ms target without disguising a miss.

2. **Stroke — `stroke` / `100002`.** Supply face/arm/speech symptoms and last-known-well information. Assert `FAST_STROKE` reaches the correct clinic's nurse, the facts cite patient input, and unavailable/slow urgency inference cannot suppress the alert. Nurse decision completes the same persisted handoff. Assert another clinic/session cannot see it; no external stroke-team contact is claimed without an implemented integration.

3. **Drowsy child — `child-drowsy` / `100003`.** Use the parent/child route and report hard-to-wake (also test “not sure”). Assert `NG143_RED_FLAG`, child wording, skipped inappropriate sensors and no adult-model suggestion for the two-year-old. Nurse supplies the decision; briefing and HAPI preserve parent-stated facts and the absence of unsupported measurements. Repeat with camera/voice declined and assert zero media access.

4. **Sepsis — `sepsis` / `100004`.** Enter infection symptoms and the explicitly simulated septic kiosk measurements. Assert partial NEWS2 and `SEPSIS_NEWS2`; missing respiratory rate/BP/consciousness remain unknown. Add nurse measurements, verify recalculation/provenance, nurse decision and consistent HAPI/briefing data. Disconnect the EHR during a variant: show unavailable/write-back failure honestly while retaining the nurse alert and recoverable write intent.

5. **Routine walk-in — `routine` / `100005`.** Complete a normal consenting text/tap intake and one real-voice variant. Verify actual history/appointment lookup, no invented danger flag, nurse confirmation, exactly one token, configured department/room, live board calling and doctor briefing. Mark arrived then close, and verify room release. A data-consent refusal variant creates no patient session or FHIR visit.

6. **QR phone continuation — `qr-phone` / `100006`.** Answer two kiosk questions, issue/claim the QR in a separate phone browser context, and continue the same session. Assert replay/expired/forged claims fail, old kiosk token is revoked, kiosk clears within the documented six-second journey criterion, and token appears only in the claim body. Refresh mid-intake and resume the pending question from backend state without another claim. Finish, nurse-confirm, observe queue changes and socket reconnect/poll fallback. Ask a sourced practical clinic question and an unanswerable question; a medical question must be refused and flagged. Press “I feel worse” twice and verify both persisted reports reach the nurse, including after confirmation. Run an Arabic variant, preserving language/RTL without an accuracy claim.

7. **No-show — `no-show` / `100007`.** Complete intake and nurse confirmation, then call into a compatible room. Advance controlled server time or wait through the configured grace window: first miss releases the room and requeues once; call again and expire the window to reach `no_show`. Assert one recall, correct outbox/event delivery, updated board/phone status, no duplicate active token and isolation from other clinic queues. Verify a new eligible patient can use the released room.

Shared negative gates: patient cannot finalize urgency; kiosk/phone cannot act as staff; consent revocation closes media paths; raw audio/camera frames are not retained; briefing facts have verifiable provenance; provider failure is visible; medical advice never appears in patient output. A skipped shared safety gate keeps the affected journey incomplete.

## Updating this guide

Add verification results only with the exact commit and evidence path. Keep source findings, earlier reported results and fresh executed checks separate. Update the first implementation's status only after its tests pass; this guide does not set Linear issues to Done.
