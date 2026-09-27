# CareFlow: engineering conventions

> **Agents: follow `docs/AGENT_RUNBOOK.md`** for how to pick the next issue, decide without the owner, retry, commit and report.

This is the single source of truth for every coding agent working on CareFlow. Read it and `docs/PLAN.md` before starting any Linear issue.

Research prototype. Synthetic data only. Not for clinical use. This label appears in every UI footer and in the README.

## Owner decisions, 26 Sep 2026 (binding; override anything in PLAN.md)

- **Zero cost.** No paid services, no paid or rented GPU, no paid plan upgrades. Everything runs on the MacBook Pro M3 Pro, 18 GB.
- **Data:** synthetic data (Synthea, generated scenarios) plus the public KTAS and COUGHVID datasets only. No MIMIC-IV-ED and no PhysioNet.
- **Arabic:** no native Arabic reviewer. Arabic is checked by machine (back-translation) only. No Arabic accuracy percentage appears anywhere (UI, README, reports' headline tables, portfolio card). Arabic metrics may be computed but are published as "machine-checked only; not an accuracy claim".
- **Models:** local Qwen3.5-9B 4-bit MLX is the default for every live task; providers stay pluggable. API providers are optional and off unless the owner adds a key.
- **RAG runs on the patient's phone only** (after the QR hand-over). No kiosk ask-the-clinic.
- **Repos:** `github.com/ShebinKMohan/careflow` stays private; never change its visibility or settings. TraceBisect is public, is fetched anonymously (no token) and is never modified (no pushes, issues or PRs there).
- **Speed targets (confirmed):** alert p95 <= 1000 ms; voice first-audio p50 <= 1500 ms (cloud mode) and <= 2500 ms (local mode). Measured numbers are published whether or not they meet the targets. Targets are never CI gates.
- **Ignore these parts of PLAN.md:** §5 Day 0 (PhysioNet, native reviewer, GPU rental) and Days 8–10 (native review, MIMIC swap), the native-speaker review in feature #4's proof, §4's reviewer note, "your own voice" recordings, Kaggle/Colab GPUs, Qwen3.8-27B on vLLM, and the "Default: a free-tier hosted LLM" line in PLAN's zero-cost update. Where the older body of PLAN.md conflicts with its "Update (26 Sep)" sections or with this list, this list wins, then the updates.

## Machine and cost limits

- Dev machine: MacBook Pro M3 Pro, **18 GB RAM**, ~68 GB free disk. **No paid GPU and no paid services.**
- Keep the total Docker memory at or below 6 GB. Keep one local LLM loaded at a time.
- Local LLM: **Qwen3.5-9B, 4-bit MLX** (`mlx-community/Qwen3.5-9B-MLX-4bit`, revision pinned; fallback `mlx-community/Qwen3.5-4B-4bit`), served by `mlx_lm.server`, which is OpenAI-compatible, on port 8081.
  - API providers are optional and off by default; the owner may add free-tier keys later. An API task with no key raises `ProviderUnavailable` naming the task; callers fall back to the local or replay provider, or mark the work `not_run`.
  - Subscription CLIs (Claude Code, Codex) are for **offline jobs only**, never the live path and never in CI. At most 200 CLI calls per issue.

## Repo layout (monorepo at `~/CODES/CursorFiles/careflow`)

```
apps/
  kiosk/        Next.js + TypeScript. Kiosk check-in UI. Later wrapped by Tauri 2.11 (M3)
  staff/        Next.js. Nurse station, doctor briefing, queue board, admin dashboard
  companion/    Next.js PWA. Phone after QR scan: intake, RAG chat, queue status, "I feel worse"
services/
  api/          FastAPI (Python 3.12). Public REST API + WebSocket events. Owns Postgres
  intake/       LangGraph 1.2 intake graph (Python package imported by api)
  rules/        Pure-Python safety rules engine (no I/O, no AI)
  triage/       Urgency model: training, evaluation, ONNX export, inference wrapper
  voice/        Copy of ~/CODES/CursorFiles/plivo-voice-agent (own code), extended with a kiosk channel
  rag/          Clinic knowledge ingest + hybrid retrieval (M2)
  integration/  FHIR R4 client + adapters, webhooks outbox, CDS Hooks, SMART, HL7 via OIE (M4)
  forecast/     Arrival forecasting + cough-surge job (M5); keeps torch/Chronos out of api
packages/
  contracts/    Source of truth: OpenAPI 3.1, JSON Schemas (facts, events), FHIR mapping table
  providers/    Model provider layer (Python): local / API / CLI adapters
  telemetry/    OTel setup + OTLP-JSON file exporter (M5)
config/         providers.yaml, rooms.yaml, intake.yaml, triage_scales.yaml, hospitals/demo_hapi.yaml
infra/          docker-compose.yml, HAPI FHIR, Synthea loader, OIE, Langfuse configs
eval/           Datasets (synthetic), eval harness, reports (markdown + JSON)
e2e/            Playwright end-to-end journeys (M1-19, M2-12) and the demo script
scripts/        Repo scripts: prereq and health checks, banned-deps check, memory budget
docs/           PLAN.md, CONVENTIONS.md, ARCHITECTURE.md, decisions/ (ADRs), DPIA.md, THREAT_MODEL.md
```

- **Python layout:** `services/<name>/src/careflow_<name>/`, tests in `services/<name>/tests/` (same for `packages/<name>/`). The uv workspace members are every Python folder above, including `packages/telemetry` and `services/forecast`.
- **App layout:** source lives under `apps/<app>/src/` (e.g. `apps/kiosk/src/lib/`, `apps/kiosk/src/components/`, `apps/staff/src/app/`); tests in `apps/<app>/tests/`.
- **CLI entrypoints:** `careflow` (providers, dev tools) and `careflow-api` (audit, obs), plus per-service CLIs such as `careflow-triage`, `careflow-eval` and `careflow-forecast`.
- **API:** every REST route is under `/v1` (except `/health`, `/.well-known/*` and the CDS Hooks paths). WebSockets authenticate with a one-time ticket from `POST /v1/ws-ticket`; never put a JWT in a URL.

## Tooling (pin every version in lockfiles)

- **Python 3.12:** `uv` (workspace, `uv.lock` with hashes), `ruff` (lint + format), `pyright` (types), `pytest`, `hypothesis` (property tests). Pydantic v2 for every schema.
- **TypeScript:** `pnpm` workspace, Next.js latest stable (App Router), Tailwind, `zod`, Playwright (E2E), Vitest.
- **Types are generated from `packages/contracts`:** `datamodel-code-generator` for Python, `openapi-typescript` for TS. Never hand-edit generated files.
- **Infra:** Docker Compose. Postgres 17 (pgvector, pg_textsearch for BM25), Redis/Valkey, HAPI FHIR 8.12.1 (R4), Synthea v4.
- **Forbidden:** LiteLLM (compromised on PyPI in Mar 2026), unpinned dependencies, secrets in git, real patient data.

## Ports

| Port | Service |
|---|---|
| 3000 | kiosk |
| 3001 | staff |
| 3002 | companion |
| 8000 | api |
| 8090 | voice |
| 8081 | mlx_lm.server |
| 8080 | HAPI FHIR |
| 5432 | Postgres |
| 6379 | Redis |
| 3100 | Langfuse (M5) |
| 6661, 6662 | OIE MLLP listeners (M4) |
| 8097, 8443 | OIE admin HTTP / HTTPS (M4) |
| 8095 | SMART launcher (M4) |
| 8098 | sample webhook receiver (M4) |
| 8094 | read-only FHIR gate for MCP (M4) |
| 8099 | FHIR MCP server over HTTP (M4) |

OIE: the Docker image may still be 4.5.2 until a 4.6 tag is published; pin it by digest either way.

## Model provider config (`config/providers.yaml`)

```yaml
tasks:
  conversation: { provider: local, model: qwen3.5-9b-4bit }
  extraction:   { provider: local, model: qwen3.5-9b-4bit }
  summary:      { provider: local, model: qwen3.5-9b-4bit }   # api is an optional override
  eval_judge:   { provider: cli,   tool: codex }   # offline only; fallback local; never the same model as the generator
  test_data:    { provider: cli,   tool: codex }   # offline only; or the coding agent writes the data in-session
  translation_check: { provider: local, model: qwen3.5-9b-4bit }   # offline only (M2-01); cli or api optional
speech:
  stt: { provider: mlx-whisper, by_language: { en: mlx-whisper, ar: mlx-whisper } }   # cohere-transcribe-arabic is gated on HF: use only if downloadable
  tts: { provider: kokoro, by_language: { en: kokoro, ar: mms-tts-ara } }   # Kokoro has no Arabic; SILMA cannot share numpy 2 (ADR-033)
```

- `provider` is the default; `by_language` overrides it per session language. Deepgram is an optional STT.
- Other config files: `config/rooms.yaml` (queue, rooms, grace window), `config/intake.yaml` (`llm_phrasing`, `briefing_summary`), `config/triage_scales.yaml` (scale display mapping), `config/hospitals/demo_hapi.yaml` (per-hospital FHIR adapter).

- Every LLM output is validated against a Pydantic model from `packages/contracts`.
- On failure: retry once with the validation error, then fail safe. The rules engine still runs, and the nurse sees "extraction incomplete".

## Autonomy defaults (apply in every issue)

- **Never stop to ask the owner.** Take the documented default, record it in an ADR (`docs/decisions/`) and in the Linear comment, and continue. The only human stops are listed in `docs/AGENT_RUNBOOK.md`.
- **Missing prerequisite** (no KTAS file, gated model, no API key, no owner clips, Docker down, fetch blocked): mark the affected suite or test `not_run` (or skip it via a `requires_*` marker) with the reason, comment on the Linear issue, and continue. Never estimate a missing number.
- **Contracts win.** `packages/contracts/openapi.yaml` is the single source for routes, roles and bodies (M0-03 lists them). A missing route, role or field is added to the contract, regenerated and noted in the PR; never ask.
- **Review of generated data is automated:** schema validation, rule consistency, a second-model check against the review checklist (a different model from the generator), a PHI/real-name denylist, and for Arabic the M2-01 back-translation check. Record `reviewed_by: "agent:<tool>/<model>"`, `review_method: automated`, `reviewed_at`. Reports say "reviewed by automated checks, not clinicians or native speakers". Owner spot-checks are optional and never block.
- **No GUI steps.** Replace manual browser, desktop-app or screen-recording checks with Playwright (headless Chromium, fake media flags) or REST/CLI calls. Never run `sudo`, `mkcert -install`, `launchctl load`, or change macOS or Docker Desktop settings.
- **Hugging Face:** before any download, call `huggingface_hub.model_info(repo)`; if it is gated, private or returns 401/403, skip it with `not_run: gated model, owner HF token needed` and use the documented fallback. Pin every revision.

## Non-negotiable safety rules

1. The **rules engine runs on the structured facts before any urgency suggestion** and can never be overridden by a model.
2. **No AI output reaches a patient as medical advice.** RAG refuses medical questions and flags the nurse.
3. **The nurse confirms every urgency level** (LangGraph `interrupt()`).
4. **Every fact in the doctor briefing carries a provenance tag:** `EHR`, `PATIENT_STATED`, `DEVICE_MEASURED`, `NURSE_CONFIRMED`, `MODEL_INFERRED`.
5. **No camera frame or raw audio is ever stored.** Voice transcripts are kept only for the session.

## Definition of done (every issue)

- Code, tests, and docs updated.
- `uv run pytest` and `pnpm test` pass.
- Lint and type checks are clean.
- Acceptance criteria in the issue are demonstrably met: write `docs/evidence/<CODE>.md` (commands plus trimmed output; Playwright screenshots under `docs/evidence/<CODE>/`) and post the done report (format in `docs/AGENT_RUNBOOK.md`) as a Linear comment.
- Merge only after `gh pr checks <pr> --watch` exits 0. Never merge a PR with failing checks. (Branch protection is not available on a free private repo, so the agent enforces this itself.) If Actions is blocked by the minutes quota, run the same targets locally, save the output as evidence, and note "CI criterion deferred: Actions quota".
- No new unpinned dependency.
- Small, focused commits with clear messages.
