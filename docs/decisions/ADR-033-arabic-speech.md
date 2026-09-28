# ADR-033: Arabic speech: engines per session language, MMS-TTS for Arabic, Cohere STT behind the owner's access

## Status

Accepted, 27 Sep 2026 ([M2-02]). This is the core build. The tests and the speech eval (`eval/speech/*`, `eval/reports/speech.json`) followed in the test phase, 28 Sep 2026: see ADR-058. That phase also changed where a spoken turn ends (`speech.turn`, per language) and stopped near-silence from becoming a turn.

## Context

Since [M2-01] a patient can choose Arabic, but the voice service spoke only English: the kiosk `start` message allowed only `"en"`, and the kiosk hid the microphone in Arabic. The issue asks for:

- mlx-whisper for Arabic and English;
- Cohere Transcribe Arabic as an Arabic-only STT;
- SILMA TTS for Arabic, with MMS-TTS as the fallback;
- the engine picked per session language from `config/providers.yaml`, where an unknown provider raises at startup.

Several constraints shape it:

- The system must cost nothing and run locally.
- Third-party terms are never accepted, so a gated model is `not_run`.
- No audio is stored.
- The local LLM already uses about 6 GB.
- A download over 1 GB needs at least 15 GB free.

## Decision

### Engines per session language

- **Selection.** `speech.stt|tts.{provider, by_language}` picks each language's engine. The kiosk `start` message carries the session language (`en` or `ar`). `LocalKioskSpeech.stt(language)` and `tts(language)` build that session's engines. The kiosk now offers the microphone in both languages.
- **Startup checks.** `SpeechConfig.validate()` resolves every language's engines at startup. It raises for an unknown name, or for an engine used outside its language (Cohere or MMS for English). If the Cohere STT is configured, it must already be in the local Hugging Face cache, or the service refuses to start. Nothing falls back silently.
- **Where selection lives.** The kiosk channel's factory is `kiosk/speech.py`. The agent's `providers/factory.py` (`_build_stt`, `_build_tts`) serves only the Plivo phone channel. That channel is off by default and out of scope for M2-02, so it stays English-only.

### Speech-to-text

- **mlx-whisper.** `whisper-large-v3-turbo`, already local, transcribes both Arabic and English. The session language is passed explicitly; the model never auto-detects it. One model serves both languages, so the default configuration keeps a single STT model in memory.
- **Cohere Transcribe Arabic.** This is `CohereLabs/cohere-transcribe-arabic-07-2026` at revision `c3e911b42149bf7a1e53d5cef9878aee87515a23`: Apache-2.0, 2B parameters in BF16, 4.1 GB. It is gated (`gated=auto`).
  - **Not downloaded.** `model_info` ran before any download. The model was not downloaded, and its terms were not accepted.
  - **The class.** `providers/cohere_transcribe_stt.py` follows the model card: `AutoProcessor` and `CohereAsrForConditionalGeneration`, 16 kHz input, `language="ar"`, `max_new_tokens=256`, no remote code, transformers 5.4 or later. It sits inside `MlxWhisperSTT`'s turn logic (Silero VAD, Smart Turn v3, the 30 s cap).
  - **Loading.** It loads only from the local Hugging Face cache and never downloads at runtime. It runs on MPS when available, else CPU. It loads once per process, on first use, in the STT worker thread, and is warmed once.
  - **Eval.** Its eval rows are `not_run: gated model, owner HF token needed`.
  - **Owner step to enable it.**
    1. Log in to Hugging Face.
    2. Accept the terms on the model page.
    3. With at least 15 GB free, run `hf download CohereLabs/cohere-transcribe-arabic-07-2026 --revision c3e911b42149bf7a1e53d5cef9878aee87515a23`.
    4. Set `speech.stt.by_language.ar: cohere-transcribe-arabic`.

### Text-to-speech

- **SILMA does not fit the workspace.** SILMA TTS (`silma-ai/silma-tts`, Apache-2.0, ungated) cannot share this workspace. Its `silma-tts` package pins `numpy<=1.26.4` and pulls in gradio and nemo_text_processing, while the workspace uses numpy 2.5. The issue's documented fallback is used instead.
- **MMS-TTS Arabic.** This is `facebook/mms-tts-ara` at revision `afba26a4b470f9ae64aeeea91b99f080dee410de`, ungated, through transformers' `VitsModel`.
  - **Licence.** CC-BY-NC-4.0, which allows **non-commercial use only**. That fits this research prototype, but any commercial use needs another Arabic TTS.
  - **Tokenizer.** It takes Arabic script directly; no romanisation is needed.
  - **Files.** `download_models.sh` fetches the 145 MB weights into `services/voice/models/mms-tts-ara/` and records their sha256 in `models.sha256`. The engine loads only that directory.
  - **Output.** The model outputs 16 kHz audio, which soxr resamples to the channel rate, as Kokoro does. The text is split into sentence units (the Arabic `؟` and `؛` included) and streamed one unit at a time. A barge-in skips the units still queued.
- **English.** Kokoro stays as the English TTS.

### Dependencies and memory

- **Extra.** A new voice extra, `arabic-speech`, adds `torch` and `transformers`. Like `mlx-whisper`, it is limited to Apple silicon, because torch's Linux wheels would add the CUDA stack to the lock. CI installs no extras and uses the fakes. `make up` and `make voice` install it.
- **Loading.** Each speech model loads once per process. Every load logs the process's peak resident memory (`peak_rss_mb`).
- **Preload.** Startup preloads the English STT and each language's TTS; MMS is small enough. A configured Cohere STT loads on the first Arabic session instead.
- **Memory with Cohere.** With Cohere configured, whisper (1.6 GB) and Cohere (4.1 GB on disk) can both be in memory beside the local LLM. If the total goes above about 16 GB, the speech eval stops `mlx_lm.server` during the Cohere runs (issue step 7).

### Safety

- **No stored audio.** Audio stays in memory for one turn, as before. The new engines write nothing to disk.
- **No accuracy claim.** Arabic speech quality is not validated. The eval voices its clips with TTS, and its numbers are never presented as real-world Arabic accuracy.

## Consequences

- With the default configuration, an Arabic-speaking patient can speak and hear Arabic at the kiosk, fully on the local machine.
- MMS's non-commercial licence is recorded here and in `download_models.py`.
- Pending for the test phase:
  - unit tests, with a tiny fixture WAV and `@pytest.mark.models` on the real-model tests;
  - the factory test that switches `speech.stt.by_language.ar`;
  - the test that a session leaves no audio files;
  - `eval/datasets/speech/manifest.jsonl`, `make_clips.py`, `run_wer.py` and `run_latency.py`;
  - `eval/reports/speech.json`.
