# UI rebuild: runtime verification, 28 September 2026

Research prototype. Synthetic data only. Not for clinical use.

This is fresh local evidence from the UI rebuild session. It is not evidence of a clinical
deployment, native-speaker Arabic validation, or production readiness.

## Running stack

- Kiosk: `http://localhost:3000`; staff: `http://localhost:3001`; companion:
  `http://localhost:3002`. All answered HTTP 200 during inspection.
- API `http://127.0.0.1:8000/health` returned
  `{"status":"ok","version":"0.1.0","db":"ok","redis":"ok","fhir":"ok"}`.
- The existing API process used `LOCAL_LLM=1`. `CAREFLOW_PROVIDER_MODE` and
  `CAREFLOW_CASSETTE` were unset: live model calls, not replayed responses.
- Existing MLX server on 8081 runs the pinned Qwen3.5-9B 4-bit model revision
  `938d8919941c6e7efd3c7150eff7fe9d12afa631`.
- Authenticated admin status reported ruleset `2026.3.0` and urgency model
  `ktas-hgb-ebf31b5e-s42`. The user's existing `config/intake.yaml` was preserved.

## Backend safety and integration checks

```sh
uv run pytest services/rules/tests \
  services/api/tests/test_feels_worse_flow.py \
  services/api/tests/test_handover_security.py \
  services/api/tests/test_intake_flow.py \
  services/api/tests/test_role_matrix.py \
  services/api/tests/test_briefing_route.py -q
```

Result: **374 passed in 31.25 seconds**.

```sh
uv run pytest services/api/tests/test_consent_and_id_tap.py \
  services/api/tests/test_session_status_recovery.py \
  services/api/tests/test_queue_routes.py \
  services/api/tests/test_writeback_store.py \
  services/api/tests/test_security.py \
  services/api/tests/test_rls.py \
  services/api/tests/test_events_dead_letter.py -q
```

Result: **67 passed in 12.14 seconds**. Total: **441 tests passed**, no skips in these
runs. API fixtures create the separate `careflow_test` database; flow fixtures replay recorded
extraction responses while exercising the real API runtime, Postgres, Valkey and HAPI. These
tests are distinct from the live browser journeys below.

## Existing status findings and voice restoration

- The FHIR consumer was running, with eight historical failed writes in the demo clinic.
  Read-only inspection dated all eight to 27 September: seven bundles were rejected by the
  provenance safety guard, and one returned HTTP 409. More recent stored writes on 28
  September were successful. Historical failures and safety validation were preserved.
- The inherited stack explicitly used `VOICE=0`; its voice service was disabled. Installed
  model files and Python dependencies were present. The existing voice binary was started
  using `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1`, with no model download or install.
- Initial voice preload completed in 10.1 seconds. `/healthz` reported `careflow-voice`.
  The readiness check only inspected configured model files; it did not prove inference.
- A previously absent, ignored `apps/kiosk/.env.local` enables the restored voice service
  for this checkout. It contains only the public voice-enabled flag and local WebSocket URL.
  The voice process log is `.run/voice-rebuild.log`.

## Live browser verification

An initial English routine journey was interrupted while the kiosk shell was actively being
edited: hot reload discarded the intentionally memory-only patient session. Its failure
snapshot showed the check-in recovery message. This attempt is retained as a failure, and
the journey is repeated after the kiosk implementation is stable; it is not counted as a pass.

The live model then timed out once at the user's existing 20-second extraction limit while
speech models and frontend builds competed for memory. The speech process started in this
session was temporarily stopped; a real extraction of the same routine complaint then completed
in 6.77 seconds with verified complaint, injury and symptom facts. The timeout was not increased.

## Phone-first entry and LAN verification

The requested welcome screen now offers kiosk check-in and a phone QR before patient details.
The new browser journey uses a fresh kiosk context without injecting device credentials. Its
initial attempts found and led to these corrections:

- Browser streaming request bodies cannot use the HTTP/1 kiosk proxy; materializing the JSON
  request body fixed `ERR_ALPN_NEGOTIATION_FAILED`.
- A phone claim immediately navigates after saving its scoped session. The test now reads
  that resulting session rather than racing response-body access after navigation.
- Legacy direct kiosk calls no longer carry the header used only by the server-managed proxy,
  preventing unnecessary CORS preflight rejection on simulated vitals.

The supported stack configuration was switched to this machine's current Wi-Fi address,
`192.168.1.46`, preserving localhost origins. Direct LAN connections to the Python API returned
empty responses on this machine although loopback worked. The companion Next server now proxies
`/v1/*` to the loopback API, including the live WebSocket channel; browser API traffic uses
`http://192.168.1.46:3002`. The prior generated environment is preserved in the ignored
`.run/stacks/careflow.pre-ui-lan.env`. Docker services and data were not restarted or reset.

```sh
pnpm --filter @careflow/e2e exec playwright test journeys/mobile-start.spec.ts
```

Result: **1 passed in 47.9 seconds** (journey 46.6 seconds), using the live local Qwen model.
The journey verified welcome QR, phone consent and identity, first intake question through
completion, nurse decision, refusal of patient/device decision attempts, queue token and room
on the phone and public board, attributed briefing, and actual HAPI Encounter,
QuestionnaireResponse and Composition write-back. The phone used the LAN URL from a browser
on this Mac; a separate physical phone has not been observed.

Machine-readable report: [mobile-first-live.json](UI-REBUILD-20260928/mobile-first-live.json).
The briefing contained 12 attributed items. HAPI held Encounter `198485`, two Observations,
no Flags, one QuestionnaireResponse and one Composition.

```sh
pnpm --filter @careflow/e2e exec playwright test journeys/routine.spec.ts --grep '\(en\)'
```

Result: **1 passed in 48.5 seconds** (journey 46.8 seconds), using live local Qwen. This
also exercised the kiosk intake, simulated vitals, nurse override with a reason, queue and
FHIR path after the proxy/header corrections.
Machine-readable report: [routine-live.json](UI-REBUILD-20260928/routine-live.json).

The final E2E package TypeScript and ESLint checks passed. The API and both tested paths keep
authentication and provenance checks intact; no assertions were relaxed to obtain these passes.

The voice process was restored after both text journeys. Its final lifecycle and readiness
boundary are recorded below. The kiosk's voice-enabled setting matches the running service.

```sh
pnpm --filter @careflow/e2e exec playwright test journeys/consent-voice.spec.ts --grep 'agree, tap'
```

Result: **failed in 37.8 seconds**. Consent was saved before the microphone opened, and local
Whisper correctly transcribed the synthetic chest-pain fixture. Intake did not advance:
the voice client's current 10-second HTTP timeout produced `api_unavailable`, and the API
subsequently reached its existing 20-second extraction timeout with speech models loaded.
The service remains running, but this is **not a verified complete voice journey**. No
accuracy claim follows from this synthetic transcription. Follow-up work needs to align
voice/API deadlines and reduce simultaneous speech/model memory use without weakening safety.
The user's intake timeout configuration was not changed.

Machine-readable failure report:
[voice-live-failure.json](UI-REBUILD-20260928/voice-live-failure.json).

## Bounded voice repair and remaining limit

The voice HTTP deadline was raised from 10 to 30 seconds so it can wait through the API's
20-second extraction deadline plus its fallback question and transport overhead. Startup now
warms English speech engines; Arabic MMS synthesis still loads from installed local files on
the first Arabic request. It is not disabled. No model was downloaded and the user's intake
configuration was not modified.

Regression checks passed:

- Kiosk voice, configuration and speech routing suites: **175 passed in 4.72 seconds**.
- Preload, readiness, speech routing and call-route suites: **115 passed in 2.46 seconds**.
- Final timeout-budget, preload and readiness checks: **19 passed in 0.67 seconds**.
- Ruff lint and format checks passed for the six changed Python files.

These runs overlap; they are not a count of distinct tests. The new cross-service budget
assertion verifies that the voice deadline covers the API's extraction and question budgets.
Preload tests cover success and startup failures while preserving Arabic availability.

The live synthetic-microphone rerun **still failed** next-question advancement in 40.9 seconds.
The client now receives the API response and speaks its fallback, but Qwen's extraction still
hits 20 seconds with speech loaded. The measured turn was 4,107 ms speech-to-transcript,
21,676 ms transcript-to-API, and 3,044 ms API-to-first-audio (28,827 ms total). This remains a
local resource/performance limitation requiring further investigation. Full voice intake
completion is **not verified**; text and phone-first journeys passed separately above.

Machine-readable rerun report:
[voice-after-deadline-fix.json](UI-REBUILD-20260928/voice-after-deadline-fix.json).

During the bounded repair, speech temporarily ran as a detached process tracked in
`.run/voice-rebuild.pid`. That instance has now been stopped and its PID file removed.
The final reception-flow restart consolidated speech into the main honcho supervisor;
see the final lifecycle below. `/readyz` identifies model verification as `file_presence`,
with `engine_loadability: not_checked`, rather than claiming a complete voice visit passes.

## Successful bounded memory-release repair

Whisper held about 2.5 GB of MLX GPU allocations after transcription while Qwen extracted facts.
The real transcriber now serializes decoding and cleanup with a process-wide lock, synchronizes
MLX, clears the pinned mlx-whisper global ModelHolder, collects Python references and clears
the MLX allocator cache before returning the transcript. Cleanup failures are logged without
masking the transcript or its original error. Subsequent turns reload the same installed model.
English and Arabic routing remain available; no files were downloaded or intake deadlines changed.

The restarted speech process logged 2,501,024,451 bytes before cleanup and 116,302 after the
actual spoken turn. This is process-local MLX accounting, not a measurement of total machine
RAM. The lock does not coordinate simultaneous Qwen work across different patient sessions.

- Actual synthetic-microphone journey: **1 passed in 29.6 seconds** (28.0-second journey).
  Consent was saved before microphone access, Whisper recognized the chest-pain fixture, the
  live API advanced to the next question, and the stored fact retained transcript-span
  provenance. This verifies one spoken turn, not an entire voice-only visit or real-world
  clinical speech accuracy. [Report](UI-REBUILD-20260928/voice-memory-release-live.json).
- Complete typed routine **with the speech process still running**: **1 passed in 51.0
  seconds** (49.7-second journey), including nurse decision, queue, attributed briefing and
  real HAPI write-back. [Report](UI-REBUILD-20260928/routine-with-voice-live.json).
- Existing Whisper and kiosk-speech unit suites: **28 passed in 0.70 seconds**.
- New fake-only release tests: **6 passed in 0.10 seconds**, covering release ordering,
  cleanup on errors, preservation of results/errors when cleanup fails, sequential English
  and Arabic, and thread serialization through both decode and cleanup. Ruff passed.

No test servers remained listening on ports 3100–3102. The standalone voice PID was moved
out of `.run/stacks` because that directory identifies app supervisors to `make up`. Its
temporary standalone lifecycle was replaced by the unified supervisor described below.
The primary reception UX was subsequently revised in response to the user's new direction; the older full-journey passes document runtime behavior, not proof of that new UX.

## Final runtime lifecycle after reception integration

The required API restart loaded the new no-LLM reception route and consolidated all six
services under **honcho PID 48882**, tracked in `.run/stacks/careflow.pid`. Voice is now its
child **PID 48893**. The previous standalone voice process was stopped;
`.run/voice-rebuild.pid` was removed. `make down` now owns speech as well as the rest of this
stack. Runtime logs are `.run/stacks/careflow.log`; `.run/voice-rebuild.log` is historical
repair evidence. All six health checks passed after the restart. API health also verified
Postgres, Valkey and HAPI. Docker services/data were not restarted or reset.

The generated stack environment enables `SIM_BP_CUFF=1` so the guided demo receives actual
simulated cuff values from the existing device scenario. The guided vitals page displays
'Demo readings' and explicitly states Mac Touch ID cannot measure vitals or provide sensor
access. The browser does not generate readings. It compares server reading timestamps to
the baseline, reveals the saved values in stages over ten seconds, and automatically
continues only when fresh required readings are available. A 35-second absolute deadline
aborts stuck requests; errors offer retry while preserving the visit.

Guided vitals unit suite: **10 passed in 1.07 seconds**, including sequence timing, exactly
once completion, stale readings, missing cuff values, retry, failed polling, incomplete
readings, unmount cleanup, hung-request abort, and Arabic controls. Focused ESLint and
TypeScript checks passed. New reception browser evidence is recorded separately under
`docs/evidence/RECEPTION-20260928`; the earlier UI journeys are not represented as proof of
the new reception flow.

The subsequent provenance-span correction was loaded by another process-only restart,
with all six health checks passing. The final supervisor/voice PIDs above reflect this
restart. Guided vitals now also offers **Back to visit summary** for a saved reception
visit. This unmounts measurement timers and displays a local read-only summary; **Return
to health check** retains the same visit, without edits or resubmission. The focused
page/panel suite passed **11 tests in 0.69 seconds**, with final TypeScript and ESLint checks
passing. New browser evidence remains the separate reception suite's responsibility.

## Reception queue delay: evidence and final correction

The previous fresh reception journey reached a nurse decision in 902 ms, but the API's
OpenTelemetry server spans recorded two patient status requests taking 16.678 and 16.437
seconds. Both eventually returned HTTP 200. This locates the delay inside the API, rather
than establishing a browser/LAN proxy failure. The exact internal blocking operation was
not separately instrumented. [Historical spans](RECEPTION-20260928/status-timeout-spans.json).

The backend's subsequent change keeps reception briefings deterministic, avoiding the
optional model summary while retaining sourced sections. After loading that patch with
a process-only restart, QA's fresh two-path browser suite passed: nurse decision 457 ms,
queued patient status 144 ms, and an actual visible queue code. Kiosk registration was
230 ms with a 10.456-second guided demonstration; phone registration was 122 ms. See
[stage timings](RECEPTION-20260928/stage-timings.json) for the QA-owned full evidence.

A read-only comparison of the previously slow saved session returned queued status in
28.0 ms directly at port 8000 and 37.2 ms through the LAN port-3002 proxy; concurrent API
health took 78.3 ms. [Probe results](RECEPTION-20260928/status-latency-probes.json).
The final supervisor is **48882** and voice child **48893**. All six health checks passed
after this final reload; no further runtime or source changes are pending.
