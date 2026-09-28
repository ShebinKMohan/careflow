# ADR-058: Speech verified: where a spoken turn ends, no turn from silence, the speech eval

## Status

Accepted, 28 Sep 2026 ([M2-02], the test phase ADR-033 deferred). Evidence: `docs/evidence/M2-02.md`.

## Context

ADR-033 built the engines: mlx-whisper for English and Arabic, Kokoro for English, MMS-TTS for Arabic, and Cohere Transcribe Arabic behind the owner's Hugging Face access. It deferred the tests and the speech eval. The Claude Code handoff of 27 September asks for:
- fixture tests of the adapters and the factory, and separate checks on the real local models;
- WER, CER and p50/p95 latency with exact models, revisions and clip provenance;
- TTS clips treated as optimistic machine evidence, never an Arabic accuracy claim;
- Cohere `not_run` while gated;
- language selection, microphone denial, interruption, reconnection and error recovery verified in the actual voice service;
- no raw audio left after a session;
- the Arabic voice's non-commercial licence recorded as a boundary.

The first real-model check found a defect ADR-033's fixtures could not show. A TTS-voiced "عندي ألم في صدري من ساعة." ("I have had chest pain for an hour") reached the kiosk as "عدي": the turn ended after the first word.

## Decision

### Where a spoken turn ends, per language

- **What was wrong.** A turn ended when Smart Turn v3 judged the sentence complete after 200 ms of silence, or after 400 ms of silence without it (the M1-14 settings). At the 0.5 threshold Smart Turn judged almost everything complete:
  - every Arabic prefix scored 0.89 to 0.99;
  - unfinished English ("My phone number is", "I have chest pain and") scored 0.63 to 0.72, and "Well, um, I think it" 0.98.

  So every turn ended at the first 200 ms pause. On a kiosk, the first words would be posted as the whole answer, and the rest as the answer to the next question.
- **Measured.** `eval/speech/run_turns.py` feeds every clip of the speech eval (below) through the voice service's own turn logic: the real Silero VAD, Smart Turn and turn tracker, with a stub transcriber. Answers split into two or more turns:

  | Settings | English (261 answers) | Arabic (506 answers) |
  | --- | --- | --- |
  | M1-14: Smart Turn at 0.5 after 200 ms | 30 | 41 |
  | Smart Turn at 0.9 | 4 | 29 |
  | Silence only, 400 ms | **0** | 27 |
  | Silence only, 550 ms | 0 | 8 |
  | Silence only, 700 ms | 0 | **3** |
  | Silence only, 850 ms | 0 | 2 |

- **Decision.** `config/providers.yaml` sets `speech.turn` per language:

  ```yaml
  turn:
    en: { smart_turn: false, endpoint_silence_ms: 400 }
    ar: { smart_turn: false, endpoint_silence_ms: 700, hard_silence_ms: 1200 }
  ```

  - English ends after 400 ms of silence: no English answer split.
  - Arabic ends after 700 ms: 3 of 506 split. Those are MMS pauses of 704 and 864 ms, two of them after "إي،" ("yes,").
  - Going from 700 to 850 ms saves one more answer and costs every Arabic turn 150 ms, so it was not taken.
- **The cost.** Each turn waits longer before transcription: 400 ms (English) and 700 ms (Arabic) instead of the 200 ms Smart Turn allowed. Measured from the end of speech to the transcript, p50 is 1,155 ms in English and 1,560 ms in Arabic. An answer cut in two costs far more: a lost symptom, or a fragment extracted as the next answer.
- **Startup checks.** Settings that cannot work fail at startup: an unknown language, a hard end before the silence end, or a Smart Turn check after the end. A language without an entry keeps the service's settings, and `smart_turn: true` brings Smart Turn back for a language.
- **Limits.** The settings are chosen on TTS-voiced answers: patients pause more, and differently. They are a measured default, and native recordings are needed to confirm them (M6-01, or the owner).

### No turn from near-silence

- **Whisper writes words into silence.** On digital silence and faint noise, the local mlx-whisper returns "Thank you." in English and "ترجمة نانسي قنقر" (a subtitle credit) in Arabic. It reports `no_speech_prob` 0.0 and a high `avg_logprob` for these, so its own signals cannot catch them.
- **A phantom turn.** After each turn the Silero VAD is reset. On one clip, the reset VAD scored MMS's trailing noise (about -70 dBFS) at 0.3 to 0.7 and opened a second turn of near-silence. On a kiosk, Whisper would have turned it into an answer the patient never gave.
- **Decision.** A frame under -60 dBFS (RMS 0.001) never counts as speech (`MIN_SPEECH_RMS`). The VAD still sees every frame, so its state is unchanged.
  - The phantom is gone from every setting above.
  - A fixture test keeps it: a VAD that calls everything speech, fed faint noise, never reaches the transcriber.
  - The floor does nothing about louder noise. That is still the VAD's job, and real rooms are not in these clips.

### A speech engine that stops mid-session

Before, the kiosk loop saw the transcript stream end and waited: the patient spoke to nothing. Now the voice service sends `speech_unavailable` and closes with 1011, exactly as when the engines fail to start. The kiosk marks voice failed and stops listening, and the patient types or taps.

### Cohere as its own class

`CohereTranscribeSTT` subclasses `MlxWhisperSTT`, so changing only `speech.stt.by_language.ar` visibly switches the class. A 401 or 403 from the Hub now also maps to "gated model, owner HF token needed". The model is still not downloaded, and its eval rows are `not_run`.

### The speech eval (`eval/speech/`)

- **Clips.** `eval/datasets/speech/`, 767 rows over 530 distinct clips:
  - `sentences.yaml`: 15 Gulf and 15 Levantine Arabic, and 15 English, intake sentences. The agent wrote them; the text and the dialect labels are not reviewed by native speakers.
  - Every patient turn of the scripted scenarios: `intake_en` (246) and `intake_ar` (476).
  - **Voices.** Kokoro for English, MMS-TTS (`facebook/mms-tts-ara@afba26a…`) for Arabic.
  - **Seeding.** Each clip is seeded from its text, so a rerun is byte for byte the same.
  - **Storage.** The WAVs are 71 MB and gitignored. The manifest records each clip's origin and voice, and is committed.
- **`run_wer.py`.**
  - mlx-whisper (`mlx-community/whisper-large-v3-turbo@a4aaeec…`) transcribes each clip whole, in its language.
  - Both sides are normalised as the issue says: Arabic without harakat or tatweel, with أ إ آ → ا and ى → ي; English lowercased; both without punctuation.
  - jiwer scores them per engine, language, dialect and set.
  - Cohere rows are `not_run` with the reason.
  - Every transcript goes to `eval/reports/speech_transcripts.jsonl`.
- **`run_turns.py`.** The table above: splits, and phantom turns apart from them.
- **`run_latency.py`.**
  - The voice service runs in-process with its real engines, and one kiosk session per language plays 20 answers over `/kiosk/audio` in real time.
  - The service's own clock times each stage, from the end of speech to the first byte of the spoken question.
  - The API is stubbed and answers at once: **no model extraction is in these numbers.** M2-12 measured 6 to 8 s of extraction for a typed answer with the local model.
- **The report.** `eval/reports/speech.json`, with `publish: false`, the machine (no user name or path), every model at its revision, and the caveats.
- **The Arabic numbers.** Arabic WER and CER measure the MMS voice and Whisper together. MMS was trained on read speech and pronounces dialect words poorly ("عدي" for "عندي"). These are machine measurements, never an Arabic accuracy figure (CONVENTIONS).

### Tests

- **Fixtures (CI).**
  - Cohere: the gated model is never downloaded; the model card's inference in Arabic; MPS, else CPU.
  - MMS: sentence units at ؟ and ؛; resampling; barge-in, including a unit queued behind another session; warm-up once; a missing model or extra.
  - The factory: switching the Arabic STT class; each language's turn settings; startup failures.
  - The kiosk socket: an Arabic session builds Arabic engines and posts Arabic turns; a lost connection starts again; engines that fail to start, then recover; an engine that fails mid-session.
  - The kiosk client: a denied microphone rejects `start()` and opens no socket; `speech_unavailable` and a 1011 close release the microphone.
  - The energy floor.
- **Real models (`@pytest.mark.models`).** Registered in `pyproject.toml` and skipped unless `CAREFLOW_LIVE_VOICE=1` is set on a Mac with the models:
  - English Whisper and Kokoro (`test_live_voice.py`);
  - the Arabic MMS → Whisper round trip;
  - an Arabic kiosk session on the real engines that writes no file.

### The Arabic voice's licence boundary

MMS-TTS Arabic is **CC-BY-NC-4.0**, for non-commercial use only. It serves this research prototype and the eval's clips; those clips are non-commercial artefacts too. **Before any commercial release, the Arabic voice must be replaced**:
- SILMA TTS (Apache-2.0) once it runs beside numpy 2 (ADR-033); or
- another Arabic TTS whose licence allows commercial use.

Only `speech.tts.by_language.ar` changes; the eval's clips are then voiced again.

## Consequences

- A 200 ms pause no longer ends an answer. On the TTS answers, no English answer is cut and 3 of 506 Arabic answers are. Kiosk turns wait 200 ms longer in English and 500 ms longer in Arabic, measured and reported.
- Near-silence never reaches Whisper, so its best-known hallucinations cannot become answers.
- M6-01 reruns `make_clips.py --from-scenarios`, `run_wer.py`, `run_turns.py` and `run_latency.py` before publishing, and adds the extraction time to the latency.
- **Open, owner steps:**
  - Cohere Transcribe Arabic (log in, accept the terms, download; ADR-033);
  - native recordings to confirm the turn settings and to measure Arabic recognition on real voices.
