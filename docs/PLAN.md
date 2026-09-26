# CareFlow Triage: end-to-end build plan

> **Superseded in part.** The "Owner decisions, 26 Sep 2026" section of `docs/CONVENTIONS.md` is binding and overrides this plan (zero cost, local Qwen3.5-9B default, no MIMIC or PhysioNet, no native Arabic reviewer, no GPU rental, confirmed speed targets). Coding agents also follow `docs/AGENT_RUNBOOK.md`.

26 Sep 2026. Research prototype, synthetic data only, not for clinical use.
Versions checked live on 26 Sep 2026 (see "Sources" at the end).

---

## Update (26 Sep): zero-cost setup, supersedes the relevant parts below

The whole build runs on the MacBook Pro (M3 Pro, 18 GB RAM, 68 GB free disk).

- **No MIMIC-IV-ED.** Train and evaluate the urgency model on the free Kaggle KTAS dataset (1,267 real ED records). Use Synthea fake patients for everything else. Don't train it on fake triage data: it would only learn the rules we wrote into that data.
- **No paid GPU.** Qwen3.8-27B needs about 16 GB even at 4-bit, which doesn't fit in 18 GB alongside Docker.
  - Default: a free-tier hosted LLM, allowed because all data is fake.
  - Local model: **Qwen3.5-9B**, 4-bit via MLX, about 5–6 GB. Use it for extraction and for "sovereign mode". Fallback: Qwen3.5-4B if speed is too low.
    - Qwen3.8 has no small version yet: only 27B and 2.4T-A95B, checked 26 Sep; the repo says "more sizes are coming". Swap in a small Qwen3.8 when it's released.
    - Jais-2-8B is optional, only to compare Arabic quality.
  - Speech: mlx-whisper on the Mac, with Cohere Transcribe Arabic tried locally. SILMA TTS runs on CPU.
  - Laya and the cough model fine-tune on the Mac (MPS), or on a free Kaggle/Colab GPU.
- **No native Arabic reviewer.** Check the Arabic with machine translation. The README says "Arabic supported; not validated by native speakers", and no Arabic accuracy percentage is claimed.
- **Memory budget:** run Postgres, Redis and HAPI FHIR in Docker. Start Open Integration Engine only when testing HL7. Keep one local model loaded at a time.

---

## Update (26 Sep): pluggable model providers

Every AI task picks its provider in one config file, and the admin screen shows which provider each task uses.

| Kind | Examples | Use for |
|---|---|---|
| Local | Qwen3.5-9B via MLX / Ollama / LM Studio (all OpenAI-compatible) | Default, offline "sovereign mode" |
| API | OpenAI, Anthropic, Gemini, OpenRouter | Better quality, free tiers |
| Subscription CLI | Claude Code (`claude -p --output-format json`), Codex (`codex exec --json`) | **Offline jobs only**: test-data generation, batch evaluation, labelling |

- **Tasks:** `conversation`, `extraction`, `summary`, `eval_judge`, `test_data`.
- **One interface for all providers:** chat, structured output and streaming. Every output is validated against the same Pydantic schema, with a retry, whatever the provider.
- **Built on existing code:** reuse the provider router in plivo-voice-agent. LangChain's `init_chat_model` covers the API providers. Write one small adapter for the CLIs.
- **No LiteLLM:** its PyPI package was compromised in Mar 2026.
- **Speech too:** STT and TTS are pluggable in the same way, as they already are in the voice agent.
- **Honest limits:**
  - CLIs take seconds to start, so they're too slow for live patient turns.
  - Consumer subscriptions may not permit powering a service for others.
  - Keep CLIs out of the live path.

## MVP and phases

**MVP: the smallest version that works end to end, in English.**

A fake patient (Synthea, in HAPI FHIR) checks in on a web kiosk page with the silent keypad. They type or speak their symptoms; voice uses the existing agent. Then:

1. The extractor produces structured facts.
2. The safety rules raise alerts (chest pain, FAST, sepsis with partial NEWS2, child flag).
3. A baseline urgency model (gradient boosting on KTAS) suggests a level.
4. The nurse screen confirms or overrides it.
5. The patient gets a token and room.
6. The doctor screen shows the briefing with provenance.
7. Encounter, Observations and QuestionnaireResponse are written back to HAPI.

Vitals come from a simple on-screen simulator. The model provider is switched in config.

**The MVP is done when:**
- one command starts everything;
- 3 journeys pass automated end-to-end tests (chest-pain alert, routine walk-in, sepsis alert);
- switching between the local model and an API is a one-line change.

**Phase 2, bilingual and access:** Arabic UI (right-to-left), Arabic STT/TTS, child flow, QR hand-over to the phone, consent screen.
**Phase 2b, two-way conversation (RAG + waiting-room companion). RAG runs ON THE PATIENT'S PHONE ONLY (after the QR scan); a kiosk "ask the clinic" / visitor wayfinding mode is deferred to a later stage:**
- **"Ask the clinic":** answers the patient's questions at any time.
  - RAG over the clinic's own documents (hours, departments, insurance, visitors, pharmacy, parking), in Arabic and English.
  - Tools for live facts, e.g. queue position and wait time.
  - Every answer cites its source, and it says "I don't know" when unsure.
  - **Medical questions are never answered.** They're flagged to the nurse instead.
- **Handles interruptions:** mid-intake questions are answered briefly, then the intake resumes (a LangGraph intent router).
- **On the phone while waiting:** live queue position, "ask the clinic", and a **"I feel worse" button that re-alerts the nurse.** That button is a safety feature: patients can get worse while they wait.
- **Tech:** Postgres (pgvector + BM25 hybrid), multilingual embeddings (Qwen3-Embedding), a fictional clinic handbook in English and Arabic.
- **Evaluation:** ~100 questions including trick medical ones. Measure answer accuracy, citation correctness, correct "don't know", and medical-refusal rate (target 100%).
- **Timing:** intake stays about 2 minutes. Most questions happen on the phone during the wait.

**Phase 3, on-device AI:** Laya fine-tune → ONNX on the kiosk (replacing the baseline, both published), cough model, pose hint, Tauri kiosk shell.
**Phase 4, integration depth:** HL7 ADT via OIE, FHIR Subscriptions, signed webhooks with outbox, CDS Hooks card, SMART launch, MCP server.
**Phase 5, governance and operations:** hash-chained audit log, override dataset and retraining gate, automation-bias dashboard, forecasting and cough-surge alert, Langfuse and TraceBisect, security hardening.
**Phase 6, proof:** eval harness results, README results table, architecture diagram, demo video.

---

## 0. Story fixes (apply before publishing)

1. **Speech runs on the in-country server, not the kiosk.** Good Arabic STT needs a GPU. The kiosk runs the camera, cough detection and Laya.
2. **"Zero off-device PII" → "zero data leaves the country".** Record lookups go to the in-country server.
3. **Sepsis alert: the kiosk computes a partial NEWS2** (pulse, SpO2, temperature). The nurse enters respiratory rate, blood pressure and consciousness. The demo simulates the full set.
4. **Pulse oximeter: "warns about known skin-tone limits"**, not "calibrated for" them. Software can't fix sensor bias.
5. **Label targets as targets.** Under-triage <2%, Arabic >95% and child-flag 100% are goals until measured.
6. **"Trained on ~425k US emergency visits (MIMIC-IV-ED)"**. Say US data, not UAE.

---

## 1. Architecture

```
 KIOSK (Tauri 2.11 app, React UI)                 PATIENT PHONE (PWA via QR)
 ├─ consent + silent keypad + ID-tap mock          └─ same intake, WebRTC/WebSocket session hand-over
 ├─ MediaPipe pose (tasks-vision 1.0.1, WASM)  → weak hint
 ├─ cough model (ONNX, trained on COUGHVID)    → hint
 ├─ Laya urgency classifier (ONNX Runtime int8) → suggestion
 ├─ vitals-device SIMULATOR (serial/BLE mock)
 └─ mic/speaker stream ─────────────┐
                                    ▼
 IN-COUNTRY CLINIC SERVER (Docker)
 ├─ Voice service = your plivo-voice-agent, extended:
 │     Silero VAD + Smart Turn v3 · STT: Cohere Transcribe Arabic (vLLM) · TTS: SILMA TTS
 │     channels: kiosk WebRTC, phone PWA, phone call (Plivo)
 ├─ Intake orchestrator: LangGraph 1.2 (Postgres checkpointer, interrupt() for nurse)
 │     └─ Extractor: Qwen3.8-27B FP8 on vLLM 0.30, JSON-schema structured_outputs
 ├─ Safety rules engine (plain Python, versioned, property-tested) ← runs BEFORE any AI output
 ├─ Queue/token service (rooms, departments, no-show grace window, wait estimate)
 ├─ CareFlow API (FastAPI, OpenAPI 3.1) + event bus (Postgres outbox → Redis Streams)
 ├─ Integration gateway ── FHIR R4 client (SMART Backend Services) ── Hospital EHR
 │                      ── HL7 v2.5.1 via Open Integration Engine 4.6 ── HIE (NABIDH/Malaffi style)
 │                      ── Webhooks out (signed) · CDS Hooks service · SMART app launch
 │                      ── read-only FHIR MCP server (for AI agents)
 ├─ Web apps: nurse station · doctor briefing · queue board · admin/forecast dashboard (Next.js)
 ├─ Postgres 17 (RLS per clinic) · Redis/Valkey · hash-chained audit log
 └─ Observability: OpenTelemetry (gen_ai.* attrs) → Langfuse v4 (self-host) + Grafana

 SIMULATED HOSPITAL: HAPI FHIR 8.12.1 loaded with Synthea v4 patients + OIE sending HL7 ADT
 TRAINING (offline, never live): Laya fine-tune → eval gate → versioned ONNX release
```

**The request path for one patient:** consent → identify (keypad/ID/QR) → FHIR lookup (allergies, conditions, meds) → voice/tap conversation → extractor produces structured facts → **rules engine checks danger signs first** (alert goes to the nurse instantly) → Laya suggests a level with confidence and reasons → nurse confirms or overrides (LangGraph interrupt) → token and room → FHIR write-back (Encounter, Observations, QuestionnaireResponse, Flag, Composition) → doctor briefing with provenance.

---

## 2. Two-way hospital integration

| Direction | What | How |
|---|---|---|
| Hospital → CareFlow | Patient identity, allergies, conditions, meds, appointments | FHIR R4 reads: Patient, AllergyIntolerance, Condition, MedicationStatement, Appointment |
| Hospital → CareFlow | Registration and admission events | HL7 v2 ADT A01/A04/A08 via OIE, **or** FHIR Subscriptions (R4 backport, rest-hook, id-only) with a polling fallback |
| CareFlow → Hospital | Triage visit, vitals, intake answers, alerts, hand-off, briefing | FHIR R4 writes: Encounter, Observation, QuestionnaireResponse, Flag, Communication, Task, Composition (document Bundle) |
| CareFlow → HIE | Visit registration and results | HL7 v2.5.1 ADT/ORU via OIE (NABIDH uses v2.5.1) |
| CareFlow → any system | Events: `alert.raised`, `triage.suggested`, `triage.confirmed`, `token.called`, `intake.completed` | Webhooks (Standard Webhooks style): HMAC-SHA256 signature, timestamp, replay window, Idempotency-Key, transactional outbox, retries with backoff, dead-letter queue |
| Inside the EHR | Triage card on the patient chart | CDS Hooks 2.0.1 (`patient-view`, `encounter-start`); an optional extra, not a dependency |
| Inside the EHR | Doctor opens the CareFlow briefing | SMART App Launch 2.2 (EHR launch) |
| AI agents | Read patient context | wso2 fhir-mcp-server 0.10, read-only, behind SMART |

**Adapters:** one config-driven mapping per hospital (UK Core STU2 profiles; UAE DoH data standards). Keep the internal model thin so R5/R6 can be added later. FHIR R6 is still in ballot.

---

## 3. Features: problem → solution → tech → proof

| # | Feature | Problem | Solution and tech | Proof |
|---|---|---|---|---|
| 1 | Private check-in | Details spoken aloud | Silent keypad, ID-tap mock (Emirates ID / UAE Pass style), QR hand-over to a phone PWA | E2E test of all 3 paths |
| 2 | Consent and camera | Filming without consent | Consent gate; camera frames processed in WASM, never stored or sent | Test that no frame leaves the device (network capture) |
| 3 | Bilingual voice | Language and literacy barriers | Voice agent + Cohere Transcribe Arabic + SILMA TTS + Smart Turn v3; tap fallback always on screen | WER in Arabic and English; p50/p95 turn latency |
| 4 | Structured extraction | Free talk isn't usable data | LangGraph intake graph; Qwen3.8-27B with JSON-schema outputs; every fact tagged with its source span | Field-level F1 on ~150 scripted conversations; native-speaker review |
| 5 | Child flow | Children forced through adult steps | Parent path, sensors skipped; NICE NG143 questions; drowsy/hard to wake → urgent alert | Scenario suite: all danger answers alert |
| 6 | Cough hint | Infection risk in the lobby | Small CNN trained on COUGHVID v3 (CC BY 4.0), ONNX on kiosk; on-screen mask prompt | F1 on held-out COUGHVID |
| 7 | Pose hint | Pain signs missed | MediaPipe pose, wrist-to-torso rule, labelled "weak hint" | Precision/recall on recorded test clips |
| 8 | Vitals | Hardware unavailable | Device simulator (serial/BLE mock, scenario-driven); skin-tone warning shown | Simulator in every E2E test |
| 9 | Safety alerts | Emergencies waiting in line | Rules in code: FAST (stroke), chest pain → ECG within 10 min, sepsis (infection + NEWS2 ≥5, partial at kiosk + nurse inputs), child flags; silent push to nurse (FHIR Flag + WebSocket) | Property tests (Hypothesis) + scenario suite; alert latency |
| 10 | Urgency suggestion | Slow, inconsistent triage | Laya fine-tuned on structured facts; configurable scale (MTS/CTAS/ESI); calibrated with temperature scaling; top-3 reasons | **Under-triage rate on levels 1–2**, macro-F1, ECE, vs baselines (below) |
| 11 | Nurse confirm/override | AI must not decide | LangGraph `interrupt()`; one-tap confirm/change; override reason | Override rate; "always accept" alarm |
| 12 | Queue and rooms | Crowded lobby, confusion | Token service, department/room rules, no-show grace window, wait estimate, display board | Simulation: 200 arrivals/hour |
| 13 | Doctor briefing | Doctor starts from zero | Composition with provenance tags `[EHR]` `[Patient Stated]` `[Device]` `[Nurse]`; SMART launch in EHR | 100% of facts carry a source (checked in code) |
| 14 | Hospital integration | Hospitals won't rebuild for you | Section 2 | Conformance tests against HAPI; HL7 round-trip via OIE |
| 15 | Governance and retraining | Silent model drift | Hash-chained audit log; override dataset; offline retrain → eval gate → versioned release; never live learning | Gate blocks a model that raises under-triage |
| 16 | Forecasting | Understaffed peaks | Anonymous hourly counts; statistical baseline + a time-series foundation model; cough-surge alert | Backtest on a synthetic 26-week arrival history (MAE vs seasonal-naive baseline; M5-04) |
| 17 | Privacy and residency | Legal risk | Postgres RLS per clinic; encryption; no video stored; voice kept only with consent; all in-country; pinned dependencies with hashes | DPIA-style doc + threat model in the repo |
| 18 | Observability and regression | Hidden failures | OTel traces → Langfuse; **TraceBisect** turns failed runs into regression tests | CI blocks merges on regression |

**Honest baselines for #10 (publish them all):** logistic regression / gradient boosting on the same facts, zero-shot frontier LLM, zero-shot Qwen3.8, and fine-tuned Laya. A plain tabular model may match Laya. If it does, say so. That honesty is what impresses.

---

## 4. Data and models

| Need | Source | Status |
|---|---|---|
| Patients and records | Synthea v4 → HAPI FHIR | Free, ready |
| Triage training | ~~MIMIC-IV-ED~~ (acuity 1–5, vitals, chief complaint; ~425k visits, Boston) | **Not used** (zero-cost update: no PhysioNet credentialing) |
| Triage training and eval | Kaggle KTAS dataset (1,267 Korean ED records) | Used. Cite the PLOS ONE paper, don't redistribute |
| Bilingual conversations | ~150 scripted patient scenarios (EN, Gulf and Levantine Arabic), voiced with TTS + your own voice | Generate fast; **native Arabic reviewer required** |
| Cough | COUGHVID v3 (CC BY 4.0) | Ready. Cough-E repo has no licence, so reference only |
| Forecasting | Synthetic 26-week arrival history with injected cough surges (M5-04), plus anonymous hourly cough counts (M3-04) | Ready. MIMIC-IV-ED arrival times **not used** (zero-cost update) |

---

## 5. Build plan in days (parallel lanes)

**Day 0 (start now; these block everything else):**
- Apply for PhysioNet credentialing (CITI course + reference). **Longest wait.**
- Find and book a native Gulf Arabic reviewer (1–2 days of paid work later).
- Rent one 48–80GB GPU (hourly, stop when idle) for Qwen3.8-27B + Cohere Transcribe.
- Write the contracts first: OpenAPI spec, structured-facts JSON schema, event schema, FHIR mapping table. Every lane builds against these.

**Days 1–4, six lanes in parallel (one coding agent per lane):**

| Lane | Work | Depends on |
|---|---|---|
| A. Integration | HAPI + Synthea, FHIR client + adapters, Subscriptions, OIE HL7 ADT, webhooks + outbox, CDS Hooks, SMART launch, MCP server | Contracts |
| B. Voice + conversation | Extend plivo-voice-agent: kiosk WebRTC channel, Cohere STT, SILMA TTS, LangGraph intake graph, Qwen extraction | Contracts, GPU |
| C. On-device ML | Laya fine-tune pipeline (KTAS stand-in) → ONNX int8; cough CNN → ONNX; MediaPipe hint | Contracts |
| D. Apps | Tauri kiosk + phone PWA, nurse station, doctor briefing, queue board, admin dashboard | Contracts (mock API) |
| E. Safety + eval | Rules engine + property tests, scenario suite, eval harness, test sets | Contracts |
| F. Platform | Auth/RBAC (reuse voice-dashboard control-plane patterns), RLS, audit log, OTel + Langfuse, docker compose, CI | none |

**Days 5–7: wire everything together.** Seven end-to-end journeys must pass: chest pain, stroke, drowsy child, sepsis, routine walk-in, QR phone path, no-show. Then tune latency.

**Days 8–10: prove it.**
- Full eval runs and native Arabic review.
- Swap to MIMIC-IV-ED once credentialed, and retrain.
- Forecasting backtest.
- README that leads with the results table, architecture diagram, 2-minute demo video.

**Realistic total: about 10–12 working days** if you work full-time with coding agents and the Day 0 blockers clear in time.

---

## 6. Brutal truths

- **Coding agents make the code fast, not the proof.** The finish date is set by PhysioNet approval, the Arabic reviewer and debugging audio and FHIR integration, not by typing.
- **Everything hardware is simulated,** and so is the hospital. Say so in the README.
- **The Laya results come from US data.** They say nothing about UAE patients until tested there.
- **Arabic quality is unknown until a native speaker checks it.** Cohere's model is weak at mixing Arabic and English in one sentence, so the patient picks the language up front.
- **Laya may not beat a simple tabular model.** Publish whichever wins.
- **This is decision support.** The UK regulator (MHRA) treats urgency suggestions as a medical device, so the "not for clinical use" label stays everywhere.
- **Scope risk:** 18 features is a lot. If time runs short, cut in this order: CDS Hooks → MCP server → forecasting → pose hint → phone-call channel. **Never cut:** safety rules, the nurse confirm step, provenance, or the evaluation.

---

## Sources (checked 26 Sep 2026)

FHIR versions: build.fhir.org/versions.html · UK Core: simplifier.net/hl7fhirukcorer4 · SMART 2.2: hl7.org/fhir/smart-app-launch · CDS Hooks 2.0.1: cds-hooks.hl7.org · Subscriptions backport: hl7.org/fhir/uv/subscriptions-backport · Open Integration Engine 4.6.0: github.com/OpenIntegrationEngine/engine · HAPI FHIR 8.12.1: github.com/hapifhir/hapi-fhir · Synthea v4: github.com/synthetichealth/synthea · FHIR MCP: github.com/wso2/fhir-mcp-server · Pipecat / Smart Turn v3: github.com/pipecat-ai · Cohere Transcribe Arabic: huggingface.co/CohereLabs/cohere-transcribe-arabic-07-2026 · SILMA TTS: huggingface.co/silma-ai/silma-tts · Qwen3.8-27B: huggingface.co/Qwen/Qwen3.8-27B · vLLM 0.30: github.com/vllm-project/vllm · LangGraph 1.2: github.com/langchain-ai/langgraph · Laya: huggingface.co/convaiinnovations/laya · COUGHVID: zenodo.org/records/7024894 · MediaPipe tasks-vision 1.0.1 · Tauri 2.11 · Langfuse v4 (MIT self-host) · MIMIC-IV-ED: physionet.org/content/mimic-iv-ed · KTAS: journals.plos.org/plosone/article?id=10.1371/journal.pone.0216972
