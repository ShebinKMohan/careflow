# The speech eval's clips (M2-02)

Synthetic speech only: no owner recordings and no patient audio.

- **`sentences.yaml`**: 45 intake sentences written by the agent (Claude Code) for this eval. There are 15 in Gulf Arabic, 15 in Levantine Arabic and 15 in English, each Arabic one with an English gloss. Neither the text nor the dialect labels were reviewed by native speakers.
- **`manifest.jsonl`**: one row per answer, `{id, lang, dialect, source, origin, text, audio_path, voice}`. It holds the sentences (`origin: sentences`) and every patient turn of the scripted scenarios in `eval/datasets/intake_en` (M1-18) and `eval/datasets/intake_ar` (M2-03), as `origin: scenario:<id>#<turn>`. `voice` names the TTS engine and its revision. Committed.
- **`clips/`**: the WAVs, 16 kHz mono PCM16, with 0.5 s of silence before and 1.5 s after. They are 71 MB, so they are gitignored and regenerated:

  ```bash
  uv run --extra arabic-speech python eval/speech/make_clips.py --from-scenarios
  ```

  English is voiced by Kokoro. Arabic is voiced by MMS-TTS (`facebook/mms-tts-ara`, **CC-BY-NC-4.0**: non-commercial use only, so these clips are non-commercial research artefacts). MMS is seeded from each text, so the clips come out the same every time.

TTS audio is cleaner and more regular than a patient's voice, and MMS pronounces dialect words poorly. Every number measured on these clips is a machine measurement: never an accuracy figure for patients, and never an Arabic accuracy percentage. See ADR-058 and `eval/reports/speech.json`.
