# Source of services/voice

`services/voice` is a copy of the in-house Plivo voice agent, trimmed and extended for CareFlow's kiosk (M1-14, ADR-028).

- **Source:** `~/CODES/CursorFiles/plivo-voice-agent`, commit `d8f30401217737bc930ede892fced927a167b7e1` (`git -C ~/CODES/CursorFiles/plivo-voice-agent rev-parse HEAD`, 27 Sep 2026).
- **Package:** `voice_agent`, unchanged. **Project:** `careflow-voice`.

## What was copied

- **`src/voice_agent/`:** every module. It keeps the providers (Silero VAD, smart-turn, Kokoro, Deepgram, the factory; Google Live, Sarvam, ElevenLabs, LuxTTS and sherpa-onnx left in place but not used by CareFlow), `latency.py`, `metrics.py`, the Plivo channel and its storage.
- **`tests/`:** 31 of the 38 files.
- **`pyproject.toml`:** rewritten for the workspace (below).

## What was not copied

- **Not copied at all:** `luxtts-poc/`, `models/`, `.env`, `dev/`, the Dockerfile, `docker-compose.yml`, `environment.yml` and the agent's documents.
- **Tests of the uncopied `dev/` scripts:** `test_dev_media_url.py`, `test_dev_media_scripts.py`, `test_local_smoke.py`, `test_local_speech_workflow_proof.py`, `test_live_plivo_proof.py`, `test_speech_workflow_preflight.py` and `test_voice_stack.py`.
- **`test_packaging.py`:** only its dependency check remains. Its other tests read the Dockerfile, the compose file and the deployment documents.

## Changes to copied files

| File | Change |
|---|---|
| `config/settings.py` | STT `mlx-whisper`; `public_base_url` optional (required with the Plivo flag or in production), with `plivo_base_url`; new fields `plivo_enabled` (`VOICE_PLIVO_ENABLED`), `careflow_api_base_url` (`CAREFLOW_API_BASE_URL`), `careflow_jwt_secret` (`CAREFLOW_JWT_SECRET`), `mlx_whisper_model`, `mlx_whisper_language`, `host` and `port` (`VOICE_HOST`, `VOICE_PORT`), kiosk timeouts; `deepgram_api_key` also reads `DEEPGRAM_API_KEY`; `get_settings()` returns `CareflowSettings` |
| `providers/base.py` | `TranscriptEvent.speech_ended_at` (optional; turn latency) |
| `telephony/plivo.py` | `PlivoAudioFormat.L16_24KHZ` for the kiosk |
| `providers/kokoro_tts.py` | also synthesises PCM16 at 24 kHz, Kokoro's native rate, with no resampling |
| `latency.py` | `summarize_kiosk_turn_latency_events`; the summary gains a `kiosk` section |
| `app.py` | an `assert` became an explicit error (ruff S101); the two Plivo signature paths use `plivo_base_url`; `/readyz` checks the mlx-whisper, VAD and smart-turn files |
| `main.py` | runs the CareFlow app on 127.0.0.1:8090 (the agent bound 0.0.0.0:8080) |
| `security/stream_token.py`, `storage/call_sessions.py` | `noqa: S105` on two constants that are not secrets |
| every file | `ruff format` with the repo's ruff version and settings |

## Dependencies

- **Pinned to the workspace's versions:** FastAPI 0.141, Redis 8, websockets 16 and others.
- **Dropped:**
  - `google-genai`: Google Live imports it lazily and is not used.
  - `sherpa-onnx`: the sherpa STT stays in the code but is not installed.
- **Core:** the kiosk speech stack (`onnxruntime`, `kokoro-onnx`, `numpy`, `soxr`), plus `careflow-contracts`, `pyjwt` and `pyyaml`.
- **The `mlx-whisper` extra:** `mlx-whisper`, Apple silicon only.

## Type checking

The copied files below have pyright errors that the agent's mypy configuration accepted. They are excluded in `pyrightconfig.json`; every file CareFlow added is checked:

- `app.py`, `media/session.py`, `providers/google_live.py`, `providers/sherpa_stt.py` and `storage/call_sessions.py`;
- `tests/test_call_routes.py`, `test_call_sessions.py`, `test_config.py`, `test_per_call_config.py`, `test_plivo_client.py`, `test_plivo_websocket.py` and `test_provider_factory.py`.

## Added by CareFlow

- **Source:**
  - `config/careflow.py`;
  - `providers/mlx_whisper_stt.py`;
  - `kiosk/` (`auth`, `protocol`, `turns`, `speech`, `loop`, `ws`);
  - `careflow_app.py`.
- **Scripts:** `scripts/download_models.{sh,py}` with `models.sha256`, and `scripts/make_fixture_audio.py`.
- **Tests:** `tests/test_mlx_whisper_stt.py`, `test_kiosk_ws.py`, `test_kiosk_units.py`, `test_speech_config.py`, `test_readiness.py` and `test_live_voice.py`, plus the `conftest.py` fixtures and `tests/fixtures/audio/chest_pain.wav`.
