# voice

CareFlow's voice service (port 8090): the kiosk's ears and mouth. The intake graph behind the API stays the only "brain": this service hears the patient's answer, posts it as an intake turn and speaks the next question back (M1-14, ADR-028). It is a trimmed copy of the in-house Plivo voice agent; see [SOURCE.md](SOURCE.md).

Research prototype. Synthetic data only. Not for clinical use.

## Pipeline

Browser microphone (PCM16, 16 kHz, 20 ms frames) → `WS /kiosk/audio`, then:

1. **Silero VAD and smart-turn** decide when the patient has finished.
2. **mlx-whisper** (`whisper-large-v3-turbo`) transcribes the turn.
3. **The turn is posted:** `POST /v1/sessions/{id}/turns` with the patient's token (`input_mode: voice`).
4. **Kokoro** speaks the next question (PCM16, 24 kHz) back over the socket.

Speaking over the question (barge-in) stops it; that speech becomes the next answer.

- **Engines:** chosen from `speech:` in `config/providers.yaml` (`mlx-whisper` and `kokoro`). A `VOICE_AGENT_*` variable overrides them.
- **Deepgram:** optional, needs `DEEPGRAM_API_KEY` (not set by default).
- **Not on the kiosk path:** the agent's LLM client and Gemini Live.
- **Phone channel:** the agent's Plivo routes (`/plivo/*`, `/calls/*`, `/preview/*`) exist only with `VOICE_PLIVO_ENABLED=true`, which is off by default.

## Privacy

- **Audio stays in memory** and is dropped after each turn.
- **The service stores no transcripts:** they live in the API session only.
- **The latency events** hold timings and the session id, nothing else.
- **No file is written:** a test records every `open` during a whole kiosk session (`sys.addaudithook`).
- **The patient token** travels only in the first WebSocket message, never in a URL. It is checked like the API checks it; a bad or missing one closes the socket with 1008.

## Running

```bash
uv sync --all-packages --extra mlx-whisper   # mlx-whisper: Apple silicon only
make voice-models                            # ~2 GB into services/voice/models, sha256 checked
make voice                                   # http://127.0.0.1:8090 (/healthz, /readyz, /latency/summary)
```

The kiosk turns voice on at build time with `NEXT_PUBLIC_VOICE_ENABLED=true` and `NEXT_PUBLIC_VOICE_WS_URL=ws://127.0.0.1:8090/kiosk/audio`.

Startup warms English STT and TTS. Arabic MMS TTS loads on the first Arabic voice session,
so that session can take longer to start; its language routing remains available.
Whisper releases its GPU weights and cache after warm-up and each transcription, before
the transcript reaches the API's Qwen extraction. The next spoken turn reloads the same
local model files. A process-wide lock protects Whisper's shared model holder while decoding
and releasing memory; it does not coordinate simultaneous Qwen work across patient sessions.
`/readyz` checks storage health and the presence of the configured default providers' local
model files. Its `local_speech_models` details explicitly report `file_presence` and
`engine_loadability: not_checked`; a 200 response does not prove inference or check Arabic
MMS files. Verify actual speech with the live voice tests below.

## Tests

```bash
uv run pytest services/voice                                                    # CI: fake models, real turn logic
CAREFLOW_LIVE_VOICE=1 uv run --extra mlx-whisper pytest services/voice -m live -s   # the real engines
VOICE_E2E=1 pnpm -F kiosk test:e2e --project voice                              # Chrome, fake mic, real service
```

`tests/fixtures/audio/chest_pain.wav` is synthetic speech made with Kokoro (`scripts/make_fixture_audio.py`), never a recording.
