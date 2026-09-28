# ADR-028: The voice service and the kiosk audio channel

## Status

Accepted, 27 Sep 2026 ([M1-14]).

## Context

Voice removes a literacy barrier at the kiosk. The owner's Plivo voice agent already has a proven local speech stack: Silero VAD, smart-turn end-of-turn detection, Kokoro TTS and a provider factory. The issue asks to reuse it as `services/voice` with three additions:

- mlx-whisper as the default speech-to-text;
- a browser audio channel for the kiosk;
- the phone channel behind a flag.

Two constraints shape it. The intake graph must stay the only "brain", and no raw audio may be stored (safety rule 5).

## Decision

### The port

- **What was copied.** `src/` and `tests/` were copied from the agent at `d8f3040` and trimmed ([SOURCE.md](../../services/voice/SOURCE.md) lists every change). The agent's `Settings` keeps its behaviour, and the 798 copied tests that apply pass.
- **Where the CareFlow parts live.** They are new, fully type-checked modules:
  - `config/careflow.py`;
  - `providers/mlx_whisper_stt.py`;
  - `kiosk/`;
  - `careflow_app.py`.

  Copied files with pre-existing pyright errors are excluded one by one in `pyrightconfig.json`, as the issue allows.
- **Settings.** `CareflowSettings` is the agent's `Settings` with one more source, below the environment and `.env`:
  - `speech:` in `config/providers.yaml` chooses STT (`mlx-whisper`) and TTS (`kokoro`);
  - session storage is in memory;
  - the LLM provider is `local`, which the kiosk never calls;
  - model paths point to `services/voice/models/`.

  A `VOICE_AGENT_*` variable still wins.
- **The phone channel.** `create_careflow_app()` builds the agent's app, then removes `/plivo/*` and the `/calls/*` and `/preview/*` routes that drive calls, unless `VOICE_PLIVO_ENABLED=true`. The flag needs `public_base_url`, which is otherwise optional. The unattended run never sets it.
- **Where it listens.** `127.0.0.1:8090`; the agent listened on `0.0.0.0:8080`.

### Models

- **Download.** `scripts/download_models.sh` fetches from the official releases the agent's README names, pinned by release tag or Hugging Face revision:
  - Silero VAD (sherpa-onnx release);
  - smart-turn v3.2 CPU (`pipecat-ai/smart-turn-v3`);
  - Kokoro v1.0 (the kokoro-onnx release);
  - `mlx-community/whisper-large-v3-turbo`.
- **Checks.** Hugging Face files are checked with `model_info` first: a gated model is `not_run`. A download over 1 GB needs 15 GB free.
- **sha256.** The first download records each file's hash in `services/voice/models.sha256`, and later runs verify it. The recorded hashes match the published ones for Silero (GitHub digest), smart-turn and the whisper weights (Hugging Face LFS). Kokoro publishes none.
- **No hidden downloads.** mlx-whisper loads from the local folder. An absolute model path that does not exist is an error, never a download on first use.

### mlx-whisper STT

`MlxWhisperSTT` implements the agent's `SpeechToText` protocol. It follows the sherpa STT's design: one worker thread, and end of turn decided on the audio clock by the agent's `TurnEndpointTracker`.

- **Buffering.** It buffers the turn while the VAD reports speech, with 0.3 s of pre-roll.
- **End of turn.** It commits after smart-turn says the sentence is complete (200 ms of silence), or at the silence and hard caps.
- **Transcription.** The turn is transcribed once (float32, `language="en"`, no conditioning on the previous turn). The result is one `TranscriptEvent(is_final=True, speech_final=True)`, which now carries `speech_ended_at` for latency.
- **Partials.** There are none; Whisper does not stream.

### The kiosk channel (`WS /kiosk/audio`)

- **Start.** The first message is `{type: "start", session_id, token, sample_rate: 16000}`:
  - the token is checked like the API checks it: HS256, the API's required claims, role `patient_session`, and its `session_id` claim equal to the message's;
  - the socket closes with 1008 if the message is invalid, the token is bad, or no start arrives within 5 s;
  - the token never appears in a URL.
- **Why a token and not a ticket.** CONVENTIONS' rule (a one-time ticket from `POST /v1/ws-ticket`; never a JWT in a URL) is for the API's sockets. The voice service is a separate service that posts each turn *as the patient*, so it needs the patient's own token. A ticket would still leave it needing that token, or a broader service credential. The token goes only in the first frame over the kiosk's connection: never in a URL, never logged.
- **Audio.** Then PCM16 frames (20 ms, 640 bytes; up to 16,000 bytes, even length; otherwise an `error` message) and `{type: "stop"}`.
- **`KioskTurnLoop`.** It is new; the agent's `MediaSessionRunner` is Plivo- and LLM-centric. On a final transcript it:
  1. sends `final`;
  2. posts `POST /v1/sessions/{id}/turns` (`input_mode: voice`) with the patient's token, using the contract's `TurnRequest` and `TurnResponse`;
  3. sends `question`;
  4. speaks it with Kokoro: `tts_start`, then PCM16 at 24 kHz in 100 ms frames, then `tts_end`.

  API failures become `error` codes (`unauthorized`, `session_not_found`, `conflict`, `turn_rejected`, `api_unavailable`, `bad_api_response`), and the session stays open.
- **Additions to the issue's protocol:**
  - `question` also carries the API's `next_question` (IntakeQuestion or null) and `done`, so the kiosk can show the tap answers for its key. The spoken text puts the nurse-coming notice before the question.
  - `tts_end` carries `interrupted`.
  - The start message may carry `language` (only `en` in M1).
- **Barge-in.** While a question plays, frames go to a separate Silero VAD, not to the STT, so the question's own echo is never transcribed as an answer.
  - Six speech windows (~190 ms) stop playback and send `tts_end` with `interrupted: true`, so the kiosk drops its queued audio.
  - The last 0.5 s of audio then goes to the STT as the start of the answer.
  - Playback is assumed to last as long as the audio sent, plus 250 ms.

### The browser client

- **`WsVoiceClient`** implements the M1-13 `VoiceClient`:
  - `getUserMedia` with echo cancellation and noise suppression;
  - the `pcm16-capture` AudioWorklet downsamples to 16 kHz (a box filter) and posts 20 ms Int16 frames;
  - a 24 kHz playback queue plays the question.
- **Autoplay.** Both audio contexts are created inside the patient's tap, so autoplay rules never leave them suspended.
- **Clean-up.** `stop()`, `abort()` (the idle wipe), leaving the intake screen and a server close all stop the tracks and close both contexts. Server messages are validated with zod.
- **The interface changed.** The voice service posts the answer itself, so the kiosk never posts a voice turn. `start(session, events)` reports `onTranscript`, `onQuestion` and `onError`. The kiosk shows "You said: …" and moves to the next question.
- **Other screen rules.** Tapping or typing stops listening, so one answer is never posted twice. The mic shows only for English sessions; Arabic voice is M2-02.

### Latency and privacy

- **Latency stages per turn:** end of speech → final transcript → API response → first TTS byte, and the total.
  - Each turn is logged. At the end of a session its p50 and p95 are logged too.
  - Each turn is recorded as a `kiosk_turn_latency` event (timings and session id only) in the agent's call-event store (in memory).
  - `GET /latency/summary` reports p50, p95 and max per stage in a new `kiosk` section, through `latency.py`.
- **Privacy:**
  - audio is in memory only and dropped after each turn;
  - the service stores no transcript;
  - a `sys.addaudithook` test records every `open` during a kiosk session and fails on any write, in CI with fake models and live with the real engines.

## Consequences

- **M1-12** issues patient tokens with the `session_id` claim (as M1-11 already reads it). Its turn route receives `input_mode: voice` turns from this service.
- **M1-19** starts the service with `make voice` and builds the kiosk with `NEXT_PUBLIC_VOICE_ENABLED=true`.
- **M2-02** adds Arabic engines through `speech.*.by_language` and shows the mic for Arabic.
- **M5-05** traces the voice turn stages.
- **M6-01** tunes latency: end of speech to the final transcript is about 1 s (endpointing silence plus Whisper), the largest stage.
- **Browser playback** assumes the question plays for as long as its audio. A kiosk that plays it slower would get a slightly early barge-in window.

## Update: patient-controlled hold-to-talk, 29 September 2026

The patient now holds the microphone button or the Space bar and releases it to submit one
answer. The shortcut ignores editable fields, other buttons, modifiers and repeated keydown
messages. Blur, a hidden page, pointer cancellation, leaving the screen and the idle wipe cancel
capture. The first hold may request microphone permission; releasing during that request or
socket setup cancels the attempt, so a later permission grant cannot start ambient capture.

The browser sends optional `input_mode: "push_to_talk"` in `start` and sends
`{"type":"end_utterance"}` on release. Release immediately stops all microphone tracks and the
capture AudioContext. The socket and playback context stay open for the transcript, API reply and
spoken next question; playback never starts a microphone. A new hold opens a new answer socket.
The legacy protocol defaults to `continuous` for existing clients.

The service buffers at most 30 seconds of 16 kHz PCM16 in memory, checks the existing VAD and
energy gate, and decodes the entire held utterance once only after release. Silence does not
reach Whisper or create a patient turn. Duplicate end markers and audio after submission are
ignored. A hold over the cap is discarded, never partially submitted. The UI releases at 29
seconds to leave transport headroom. No raw audio is stored; the Whisper cache release after
transcription remains in place so extraction can use the Mac's memory.

Verification: the focused client/component suite covers consent, keyboard capture, editing,
permission and connection races, release, capture shutdown and playback staying microphone-off.
The voice suite covers release-before-transcription, a single API submission despite duplicate
release, silence rejection and the legacy continuous path. Real microphone/model evidence is
recorded separately from these deterministic tests.

### Local model readiness after the first hold-to-talk live run

The first real PTT run after a cold process restart transcribed correctly but exposed the local
Qwen server's cold prefill cost: its 2,421-token extraction prompt used almost the entire
20-second patient deadline before generation. Returning HTTP 200 from `/v1/models` did not prove
patient extraction was ready.

For `LOCAL_LLM=1`, the launcher now sets `CAREFLOW_WARM_EXTRACTION=1` for the API. Before Uvicorn
accepts requests, the API uses its configured extraction provider and exact extraction schemas
on two fixed synthetic statements, one English and one Arabic. Each must produce a chief
complaint with a verified transcript span. These calls create no patient session or database
record. Both calls and model availability share a 60-second startup limit; failure prevents API
readiness rather than declaring a cold or failed model ready. The patient timeout stays at 20
seconds. Nonlocal and replay providers are not warmed.

The live gate verified English in 11.87 seconds and Arabic in 12.01 seconds. Afterward, two real
synthetic-microphone turns reached complete extraction and a new question. The first prefilled
only 78 tokens beyond its cached prompt; the second prefilled one token. End-to-end spoken
responses were still 13.00 and 14.15 seconds after release on this Mac; warming prevents the cold
failure but does not establish the project's voice latency target. Evidence is under
`docs/evidence/ADAPTIVE-20260929/`.
