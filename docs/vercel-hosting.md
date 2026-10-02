# Hosted CareFlow demo

The original patient, staff and companion apps run on Vercel. The backend is the original
FastAPI application with opt-in adapters; neither a Pi nor an always-on cloud VM is required.
All data must be fictional. This is not a clinical installation.

## Configuration

Use Vercel Hobby, Neon `free_v3`, Upstash Redis `free` with `autoUpgrade=false`, and a Google
AI Studio free-tier project with billing disabled. Do not select paid upgrades or a paid
fallback model. Keep Gemini and database credentials in server-side sensitive environment
variables. `NEXT_PUBLIC_*` variables must never contain those keys.

The backend uses Python 3.12. `scripts/package_vercel_demo.py` assembles a secret-free bundle
in `.run/vercel-api`, retaining the repository paths for YAML/templates/FHIR mappings and
pinning cloud dependencies from `uv.lock`. It includes the required SQLAlchemy asyncio and
psycopg extras and excludes local GPU/training dependencies.

1. Link the patient project with `vercel link --project careflow` and connect the free storage
   integrations. Accept marketplace terms yourself if Vercel prompts for them.
2. Pull the production storage variables into the gitignored `.env.vercel-demo` with
   `vercel env pull .env.vercel-demo --environment production`. Put `GEMINI_API_KEY` in the
   gitignored `.env`; protect both files with mode `0600`.
3. Run `.venv/bin/python scripts/setup_vercel_demo.py`. It refuses non-Neon database hosts,
   creates separate least-privilege app/migrator roles, applies migrations and checkpoints,
   seeds the fictional hospital personas and embeds the handbook for both demo clinics.
   Credentials are saved to `.env.vercel-runtime` (gitignored, `0600`), never printed.
4. Create/link a separate FastAPI Vercel project, with Fluid Compute in `iad1`, to the staged
   backend. Set the runtime variables from `.env.vercel-runtime`. On the web runtime, set
   `DATABASE_URL_MIGRATOR` to the **app** URL; migrations use the privileged role only from the
   setup script. Do not upload the owner/migrator credentials or `.env` files.
5. Set `CAREFLOW_CORS_ORIGINS` to the exact public kiosk, staff and companion origins.
   Run the packaging script, then `vercel deploy --prod` from `.run/vercel-api`.
6. The original Next.js apps use their existing root directories (`apps/kiosk`, `apps/staff`,
   `apps/companion`) with source files outside the root enabled. Include `tsconfig.base.json`,
   both JS workspace packages, `pnpm-workspace.yaml`, `pnpm-lock.yaml` and root `package.json`.
   Set `ENABLE_EXPERIMENTAL_COREPACK=1` to use the pinned pnpm version.
7. All apps need `NEXT_PUBLIC_API_BASE_URL` pointing to the deployed backend. The kiosk also
   needs server-only `CAREFLOW_API_URL`, `CAREFLOW_KIOSK_AUTO_SETUP=1` and
   `CAREFLOW_KIOSK_DEVICE_SECRET` from the dedicated demo's generated device secret.
   `NEXT_PUBLIC_COMPANION_URL` points to the companion app. Set `NEXT_PUBLIC_SIMULATORS=1`,
   `NEXT_PUBLIC_VOICE_ENABLED=true` and `NEXT_PUBLIC_VOICE_WS_URL=wss://<backend>/kiosk/audio`
   for the original synthetic sensor/voice controls. Deploy each original app.
   The staff app's `NEXT_PUBLIC_FHIR_BASE_URL` points to `<backend>/demo/fhir` for the
   existing read-only synthetic source links in clinician provenance popovers.

## Adapter boundaries

- `CAREFLOW_SERVERLESS=1`: bounded request-driven completion and per-active-socket live event
  readers. Queue expiry/retry advances when requests arrive; there is no permanent worker.
- `CAREFLOW_DEMO_FHIR=1`, `CAREFLOW_ENV=dev`,
  `FHIR_BASE_URL=https://careflow-demo.internal/fhir`: internal Postgres-backed synthetic FHIR,
  including version checks, conditional creates and transaction reference resolution.
  Other endpoints continue to use the original network FHIR client. This is not full FHIR.
- `CAREFLOW_PROVIDERS_CONFIG=config/providers.gemini.yaml`: real Gemini text through the
  original provider contract and output gates; `CAREFLOW_DEMO_MODE=0` prevents scripted replies.
- `CAREFLOW_RAG_CONFIG=config/rag.gemini.yaml`: a separate Gemini 1024-dimensional index with
  dense retrieval. Local Qwen/BM25 evaluation results do not describe this hosted index.
- `CAREFLOW_GEMINI_VOICE=1`: Live API bridge preserving the original consent/session/hold-to-talk
  contract. Audio stays in memory, and only approved next-question speech is released.
- `CAREFLOW_DEMO_ACCESS=1`: a separate `/demo` guide supplies **shared synthetic staff roles**
  without changing the existing product screens. Allowed only with dev + serverless + internal
  synthetic FHIR. Never enable it for a hospital or any private installation. It displays only
  the dedicated demo staff password; device, database, JWT and Google keys remain private.
- The trained KTAS checkpoint is not bundled. Existing deterministic alerts and nurse-confirmed
  final urgency remain available; no Gemini-created urgency score substitutes for that model.

## Verification and operations

Verify `/health` reports all dependencies healthy, then use a fictional patient to check intake,
consent, vitals, nurse review, queue assignment, source-preserving FHIR writeback and the doctor
briefing. Test QR handover, handbook citations/medical refusal and the WebSocket voice path.
Repeat reads after a new deployment to verify state survives a function replacement.

The source key shared in chat should be rotated in Google AI Studio and the backend's sensitive
`GEMINI_API_KEY` variable, followed by redeployment. No frontend rebuild is needed for key rotation.
Free quota exhaustion surfaces through existing error paths and never enables billing or a paid
model. Google free-tier data-processing terms apply; submit synthetic information only.

To return to the original local installation, unset all hosted flags/config overrides, restore
the original local database/Redis/FHIR URLs, and supply the original local text, embedding,
speech and optional trained urgency artifacts. `config/providers.yaml` remains unchanged.
