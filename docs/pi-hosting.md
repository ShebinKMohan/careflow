# Raspberry Pi hosting of the existing CareFlow application

Status on 2026-10-02: the original kiosk, staff and companion interfaces and the
original backend run locally on a Raspberry Pi 5 with 8 GB RAM. **The complete
AI experience is not ready for a public recruiter link.** The trained Laya
checkpoint is missing, and the Pi's CPU cannot complete the measured Qwen
extraction within the application's existing 20-second deadline.

No kiosk, staff or companion component, contract, workflow, clinical rule,
provider configuration or authentication policy was changed for this setup.
The explicit Linux entrypoints below adapt model execution to a CPU. The
normal Mac entrypoints remain available.

## Local deployment

The existing `infra/docker-compose.yml` supplies Postgres, Valkey and HAPI FHIR.
The Pi override at `~/.local/share/careflow/compose-pi.yml` adds container restart
policies and sets HAPI's JVM heap to 768 MB. All ports still bind to loopback.
This Pi kernel does not expose Docker memory limits; the JVM heap setting is
effective, but Docker's `mem_limit` is not enforced.

The existing migrations, intake checkpoint tables, development clinics/devices
and synthetic FHIR personas were applied. The handbook's 50 original chunks
were indexed for each of clinics A and B using the original pinned embedding model.

Python 3.12 uses the repository's locked environment plus the Linux CPU
inference dependencies: PyTorch's official CPU build, sentence-transformers,
transformers, faster-whisper and the original pinned `laya==0.3.20` package.
Native `llama.cpp` was built with OpenBLAS.

| Service | Local address | Entry point |
| --- | --- | --- |
| API | `http://127.0.0.1:8000` | `.venv/bin/python scripts/serve_pi_api.py` |
| Voice | `ws://127.0.0.1:8090/kiosk/audio` | `.venv/bin/python scripts/serve_pi_voice.py` |
| Kiosk | `http://localhost:3000` | Original production Next.js app |
| Staff | `http://localhost:3001` | Original production Next.js app |
| Companion | `http://localhost:3002` | Original production Next.js app |
| Qwen | `http://127.0.0.1:8081/v1` | Native `llama-server` |

Six service definitions are installed under `/etc/systemd/system/careflow-*.service`.
They read the gitignored, mode-600 repository `.env`. Their deployment copies
are under `~/.local/share/careflow/systemd/`. They are **not enabled at boot**
while this deployment remains incomplete. Docker's three infrastructure
containers have `restart: unless-stopped`.

The API, voice and three web apps are running. The 9B language-model service
was stopped after validation to avoid unnecessary memory pressure on the
desktop. Start it when testing AI:

```sh
sudo systemctl start careflow-llm
curl http://127.0.0.1:8081/health
sudo systemctl stop careflow-llm
```

The health check reports model loading until weights are ready. Do not run
large model tests, ingestion or builds concurrently on this Pi. An additional
8 GB swapfile exists at `/var/lib/careflow/swapfile`; it is active for this
session and is not in `/etc/fstab`.

## CPU execution adapters

`scripts/serve_pi_voice.py` injects faster-whisper into the original
`MlxWhisperSTT` transcriber port. It retains Whisper large-v3-turbo, the existing
audio protocol, consent, VAD, endpointing and language selection. CPU inference
uses int8 weights and two threads. Speech weights are released after each
utterance. Kokoro English and MMS Arabic synthesis still use the original
model files, verified against `services/voice/models.sha256`.

`scripts/pi_model_runtime.py` uses the original Qwen3-Embedding-0.6B revision,
query prompt, normalized 1024-dimensional vectors and retrieval implementation.
It selects float32 because the Pi CPU is very slow at the model's default
bfloat16 operations. The API uses a short-lived embedding worker so these
weights do not remain resident beside the 9B language model. The existing
`AskService` embedder port receives this adapter. `scripts/pi_ingest.py` runs
the original handbook-ingest command with the same CPU precision.

The Qwen runtime uses the same 9B model family through a Linux-compatible
Q4_K_M GGUF, rather than the Mac's MLX artifact. This is a runtime/quantization
change; numerical equivalence and clinical evaluation equivalence are not
claimed. No smaller model, recorded responses or alternate urgency model was
silently selected.

Pinned runtime artifacts:

| Artifact | Revision |
| --- | --- |
| `llama.cpp` | `926862e574617d5e5ab9e9c9bae317f98237f583` |
| `unsloth/Qwen3.5-9B-GGUF` | `3885219b6810b007914f3a7950a8d1b469d598a5` |
| `mobiuslabsgmbh/faster-whisper-large-v3-turbo` | `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf` |
| `Qwen/Qwen3-Embedding-0.6B` | `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` |

The GGUF is at `~/.local/share/careflow/models/Qwen3.5-9B-Q4_K_M.gguf`,
5,680,522,464 bytes, SHA-256
`03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8`.

## Configuration and remaining public deployment

The local `.env` enables the existing synthetic demo with `CAREFLOW_ENV=dev`,
`CAREFLOW_RUNTIME=live` (the existing default),
`CAREFLOW_KIOSK_AUTO_SETUP=1`, and the generated server-side device secret.
The browser receives only the original `server-managed` marker. Device
credentials must never be placed in a `NEXT_PUBLIC_*` variable or in Git.

The live Vercel kiosk is still a disconnected preview. Its original
`isCloudPreview()` intentionally disables check-in when backend/provisioning
configuration is absent. After a stable HTTPS backend route is available,
configure its existing variables and redeploy:

- `CAREFLOW_API_URL`: reachable HTTPS URL of the existing API.
- `CAREFLOW_KIOSK_AUTO_SETUP=1`.
- `CAREFLOW_KIOSK_DEVICE_SECRET`: the existing synthetic kiosk secret, server-only.
- `CAREFLOW_KIOSK_DEVICE_ID`: the seeded kiosk ID if overriding the existing default.
- `NEXT_PUBLIC_API_BASE_URL`: reachable HTTPS API address for patient-session calls.
- `NEXT_PUBLIC_VOICE_WS_URL`: reachable WSS address for the original voice service.
- `NEXT_PUBLIC_COMPANION_URL`: reachable HTTPS address of the original companion app.
- `NEXT_PUBLIC_VOICE_ENABLED=true` and the original simulator setting where required.

The backend CORS list must include the deployed web-app origins. A named
Cloudflare Tunnel can expose the API, voice and companion through HTTPS/WSS
without router port forwarding. Account/domain access is needed to establish
stable routes. The Pi's existing tunnel and its unrelated hostnames were left
unchanged. No temporary tunnel URL was added to the CV.

The original selected triage artifact was stored outside Git on the Mac:
`services/triage/artifacts/laya/laya-ktas-ebf31b5e-s42/`. Its complete checkpoint
directory, including `model.safetensors` and configuration/calibration files,
must be restored from a backup. `TRIAGE_MODEL_KIND=laya` remains selected;
without that artifact, the existing loader reports **no urgency suggestion**.
The untrained base Laya model or an HGB artifact is not equivalent to restoring
the selected checkpoint.

## Verification and limits

- All three original web applications passed production builds.
- The 37 existing focused speech/configuration tests passed.
- The four deployment entrypoints passed Ruff lint and formatting checks.
- `/health` reports database, Valkey and FHIR as `ok`.
- The original kiosk bootstrap and QR issuance authenticate against the real API.
- Browser verification passed synthetic patient-ID lookup (`100001`), birth-year
  verification (`1968`), existing visitor options, patient details, original
  consent options and entry to the intake conversation.
- A synthetic cough answer advanced through the application's existing
  extraction-timeout fallback. This is **not** a successful AI extraction.
- A standalone real Qwen probe returned correct JSON for a synthetic cough and
  duration, but took **67.65 seconds** for 38 input and 23 output tokens while
  the desktop/services were running. Actual intake has a 20-second extraction
  deadline. No timeout, safety threshold or model was changed to hide this limit.
- Original English Kokoro audio was transcribed through the CPU Whisper adapter:
  `I have a cough.` became `I have a cough.`. Cold synthesis took 13.30 seconds;
  recognition including model loading took 53.23 seconds for 1.024 seconds of audio.
- The Arabic MMS-to-Whisper probe completed, but `عندي صداع.` was recognized as
  `هذا الصداع`. Synthesis took 15.85 seconds and recognition 40.04 seconds for
  1.92 seconds of audio. This is one synthetic probe, not an accuracy result.
- The API's short-lived embedding worker returned a finite, normalized
  1024-dimensional vector; a cold query took 19.93 seconds.

Detailed local probe outputs are under `~/.local/share/careflow/`; they contain
only synthetic probe text. Full AI, Laya triage, public phone handover, public
voice and Vercel end-to-end validation remain incomplete. Keep the current
CareFlow case-study link until a complete public product demo passes verification.

## Free cloud alternatives researched

[Oracle Always Free](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
currently offers a total of 2 ARM OCPUs and 12 GB RAM. It is a candidate for a
dedicated backend, subject to capacity and account eligibility. Its performance
with the original models has not been measured. Account creation requires
owner-provided card/identity verification; no account was created here.

[Hugging Face ZeroGPU](https://huggingface.co/docs/hub/spaces-zerogpu) permits two
free hosted Gradio Spaces for a personal account in good standing with verified
email and an age above 30 days. Its shared GPU execution has queues and daily
quotas. A model service could potentially run behind the existing CareFlow UI,
but this requires an eligible existing account and a separately validated
runtime adapter. No Space or account was created, and no CareFlow requests were
sent there. Regular new Docker/Gradio CPU Spaces require a paid plan, apart from
this ZeroGPU exception.

[Render's free compute](https://render.com/docs/compute-plans) has 512 MB RAM and
is insufficient for CareFlow's full model/database/FHIR stack.
