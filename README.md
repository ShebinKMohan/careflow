# CareFlow

**Research prototype. Synthetic data only. Not for clinical use.**

CareFlow is a hospital check-in, triage and clinical workflow research prototype. Patients tap to begin or scan the welcome QR, find and verify an existing demo record or register, and identify whether they are checking in themselves or a dependent. Natural English/Arabic descriptions enter the adaptive local AI intake; department preference is optional. Hold-to-talk, an optional consented local camera preview and guided simulated vitals lead to a sourced visit receipt and provisional urgency for nurse review. Final priority and queue assignment remain with the nurse. Hospital identity and clinician availability are example data, not a booking service.

The separate legacy intake path retains six-digit patient codes, the ID-tap mock, English/Arabic text and voice intake, and an intake graph that turns conversation into structured facts. A plain-code safety rules engine checks reported facts before model output; the nurse confirms any clinical urgency. The staff workflow supports a queue token and room, a doctor briefing with fact provenance, and simulated hospital integration through FHIR R4 and HL7 v2. All patient data is synthetic.

## Quick start

Requires macOS (Apple silicon) or Linux with Docker, [uv](https://docs.astral.sh/uv/), Node 24+ and pnpm. On a Mac, `scripts/bootstrap_mac.sh` installs the missing tools (no sudo).

```bash
make up      # everything, then "CareFlow is up"
make down    # stops what this checkout started; the data volumes stay
```

`make up` checks the prerequisites and the ports, then:

1. starts Postgres, Valkey and HAPI FHIR in Docker;
2. installs the locked dependencies;
3. migrates the database;
4. loads the seven synthetic personas and the demo clinics, staff and devices;
5. trains an urgency model if none is installed (on the synthetic fixture, labelled `fixture-*`, when the KTAS data has not been downloaded);
6. starts the API and the three apps with honcho, and returns once every one answers and the API reaches its database, Valkey and HAPI FHIR.

| What | Where |
|---|---|
| Kiosk | http://localhost:3000 |
| Staff | http://localhost:3001: `/nurse`, `/doctor`, `/board?department=ed`, `/admin/status` |
| Companion (phone) | http://localhost:3002 |
| API | http://localhost:8000/health |
| HAPI FHIR | http://localhost:8080/fhir |

- **Demo sign-in.** The staff are `nurse.a@careflow.example`, `doctor.a@careflow.example` and `admin.a@careflow.example`, with the dev password that `scripts/gen_dev_env.py` wrote to the gitignored `.env` (`CAREFLOW_DEV_PASSWORD`). The same file holds the kiosk device secret.
- **Patient flow.** Open the patient app, choose English or Arabic, then **Tap to begin** or scan **Use your phone**. Returning patients find a masked record by mobile or patient ID and verify their birth year; the demo `100001` uses `1968`. New patients can register without an account. Select self, child, elderly relative or another dependent; a caregiver record never supplies the patient’s clinical history. After optional department preference and consent, hold the microphone/Space or type naturally. Suggested examples can be edited. Explicit short answers use direct parsing; free descriptions use the local extractor. The urgent-help shortcut creates a separate anonymous nurse handoff before registration, without inventing identity or age. Adults may continue to simulated vitals; children follow the nurse observation pathway. The nurse confirms urgency before queue assignment. Device/staff login is outside the patient journey.
- **Kiosk installation.** `make up` enables server provisioning for the seeded synthetic clinic. Individual app launches require `CAREFLOW_KIOSK_AUTO_SETUP=1`, `CAREFLOW_API_URL` pointing at the running API, and `CAREFLOW_KIOSK_DEVICE_SECRET` (or the inherited seeded `CAREFLOW_DEV_DEVICE_SECRET`). Set `CAREFLOW_KIOSK_AUTO_SETUP=0` to opt out. `CAREFLOW_KIOSK_DEVICE_ID` is optional for the default seeded clinic and required for another installation. The server keeps the actual device credential; the browser receives an opaque setup marker. These secrets must never use a `NEXT_PUBLIC_` prefix. If provisioning is unavailable, the welcome screen keeps its kiosk action disabled and offers retry/reception guidance. `/setup` remains an explicit staff installation route, outside the patient flow.
- **Phone entry.** Welcome QR tickets expire after ten minutes and contain clinic entry authorization, not patient details or device credentials. Multiple phones can use the displayed code; each phone creates its own visit after identification and consent. Existing-visit handover codes retain their separate single-use behavior. For an actual phone, use the same network and set the reachable host as described below; a QR pointing to `localhost` works only on the laptop. When starting the apps individually, configure `NEXT_PUBLIC_COMPANION_URL` to the reachable companion address before starting/building the kiosk.
- **Guided demo sensors.** `make up` enables simulated pulse, oxygen, temperature and a blood-pressure cuff. Set `SIM_BP_CUFF=0` to leave the cuff out. The screen labels generated readings and automatically continues after its ten-second sequence and a fresh saved result. Mac Touch ID cannot measure medical vital signs.
- **Options:**
  - `SEED_SYNTHEA=0` loads the personas only;
  - `LOCAL_LLM=1` also serves the pinned local model (Qwen3.5-9B, MLX 4-bit, Apple silicon) on :8081 for the conversation, extraction and summary, through `scripts/with_llm.sh`;
  - `VOICE=0` leaves the voice service out. It runs on :8090 when `make voice-models` has run and the port is free;
  - `CAREFLOW_STACK=<name> CAREFLOW_PORT_OFFSET=<n>` runs a second, isolated stack from another checkout: its own Docker project and volumes, and every port above plus `n` (`careflow-test` defaults to +10000). `make down` with the same `CAREFLOW_STACK` stops only that stack;
  - `CAREFLOW_PUBLIC_HOST=<this computer's address on the Wi-Fi>` lets a real phone on the same network scan the kiosk's QR code. Browser API requests use the companion's `/v1` reverse proxy on port 3002 (plus any stack offset), which forwards to the local API while preserving authentication. The QR, browser address and CORS use the reachable host (your firewall may ask to allow it). Without this setting everything stays on localhost, which a phone cannot reach.
- **End-to-end journeys.** `E2E=1 make up`, then `make e2e`. Playwright drives the kiosk, the nurse station, the doctor briefing, the queue board and a phone against the running API, Postgres, Valkey and HAPI, through:
  - the seven patient journeys: chest pain, stroke, drowsy child, sepsis, routine walk-in, the QR phone path and a no-show;
  - the three check-in paths: keypad, the simulated ID reader and the QR hand-over;
  - the consent paths, and `@ar` variants in Arabic.

  Each journey checks, in order:
  - the alert on the nurse station within 2 s;
  - the nurse's decision;
  - the token and room on the patient's screen and the board;
  - the briefing's provenance;
  - the HAPI write-back ([`e2e/lib/journey.ts`](e2e/lib/journey.ts)).

  How it runs:
  - `E2E=1` replays every model reply from [`e2e/replays/`](e2e/replays/), so no model or key is needed.
  - `E2E_LIVE=1 LOCAL_LLM=1` runs the journeys against the local model instead.
  - `E2E_RECORD=add LOCAL_LLM=1` records only the replies the cassette lacks.
  - The E2E stack's queue has a 5-second no-show grace window ([`e2e/config/rooms.yaml`](e2e/config/rooms.yaml)); the shipped window is 120 s.
  - The voice journeys need the voice service; without it they are skipped with that reason.
  - CI runs the journeys on pushes to `main`, on PRs labelled `e2e` and on demand.
- **Nightly journeys on the local model.** `make nightly-e2e` starts its own stack with the live local model, runs every journey and writes [`eval/reports/e2e_nightly.json`](eval/reports/e2e_nightly.json) (each spec's result, duration and provider). To run it every night at 02:00, `make install-nightly` installs a LaunchAgent, and `launchctl load ~/Library/LaunchAgents/com.careflow.nightly-e2e.plist` starts it. Loading it is your choice: it is a lasting change to this Mac. `launchctl unload` with the same path stops it.
- **Speech eval, on this Mac.** It needs the voice models (`make voice-models`) and the `mlx-whisper` and `arabic-speech` extras:
  - `eval/speech/make_clips.py --from-scenarios` voices the synthetic sentences and every scenario turn (Kokoro for English, MMS-TTS for Arabic);
  - `run_wer.py` writes the WER and CER of each speech-to-text engine;
  - `run_turns.py` writes how often the end of a turn cuts an answer in two;
  - `run_latency.py` writes the kiosk's p50/p95 from the end of speech to the spoken question.

  Everything goes to [`eval/reports/speech.json`](eval/reports/speech.json). The numbers are machine measurements on TTS-voiced audio, never an accuracy figure (ADR-058).
- **Everyday commands:**
  - `make logs` follows the processes;
  - `make infra` starts only the Docker services, which the database and HAPI tests need;
  - `make check` runs ruff, pyright, pytest, ESLint, tsc, Vitest and the banned-dependency check.
- **Switching a model is one line** in [`config/providers.yaml`](config/providers.yaml). For example, `extraction: { provider: api, vendor: openai, model: <model id> }` moves extraction to an API provider, once its key is in `.env` ([`test_one_line_switch.py`](packages/providers/tests/test_one_line_switch.py) proves the one-line diff).
- **Without the backend.** `make mock` serves the API contract's examples with Prism on :4010, so the apps can run against it (`pnpm -F staff dev`), and `make e2e-apps` runs their Playwright smoke tests ([`apps/README.md`](apps/README.md)).

**Limitations.** The hospital (HAPI FHIR) and the vitals device are simulated, and every patient is synthetic. The urgency model is trained on the public Korean KTAS dataset, which is not a local population. Not for clinical use.

**Languages:** English and Arabic, with right-to-left layout. Arabic supported; not validated by native speakers.

Local voice capture works on `localhost`; a real phone microphone needs an HTTPS origin. The camera preview makes no clinical observations. The active urgency model is explicitly selected with `TRIAGE_MODEL_KIND` and reports its actual artifact identity; the local demonstration selects the available fine-tuned Laya checkpoint. See [the adaptive design decision](docs/decisions/ADR-060-adaptive-patient-front-door.md) and [live verification](docs/evidence/ADAPTIVE-20260929/).

## Repository

| Path | What |
|---|---|
| `apps/` | Next.js apps: kiosk, staff (nurse, doctor, queue board, admin), companion phone PWA |
| `services/` | Python: `api`, `intake`, `rules`, `triage`, `voice`, `rag`, `integration` |
| `packages/` | `contracts` (source of truth), `providers` (model provider layer), `ui` (shared UI) |
| `config/`, `infra/`, `eval/`, `scripts/` | Config, Docker infra, evaluation, repo scripts |
| `docs/` | Plan, conventions, agent runbook, decisions (ADRs), evidence |

Design decisions are recorded in [`docs/decisions/`](docs/decisions/). Engineering rules are in [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md).

Research prototype. Synthetic data only. Not for clinical use.
