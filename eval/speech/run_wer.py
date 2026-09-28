"""WER and CER of each speech-to-text engine on the speech eval's clips (M2-02).

    uv run --extra mlx-whisper --extra arabic-speech python eval/speech/run_wer.py

- **mlx-whisper** (`mlx-community/whisper-large-v3-turbo`, the voice service's local model files)
  transcribes every clip of eval/datasets/speech/manifest.jsonl in the clip's language, never
  detecting it: English and Arabic.
- **Cohere Transcribe Arabic** runs on the Arabic clips only if its pinned snapshot is in the
  local Hugging Face cache. It is gated (the owner's login and terms), so otherwise its rows are
  `not_run` with the reason; nothing is downloaded.

Each clip is transcribed whole, as the kiosk transcribes one finished turn (eval/speech/
run_turns.py measures where turns end). Both sides are normalised the same way
(`careflow_eval.speech.normalize`) and scored with jiwer, per engine, language, dialect (Gulf and
Levantine apart) and set (the hand-written sentences, the scenario turns). It writes the `stt`
section of eval/reports/speech.json, and every transcript to eval/reports/speech_transcripts.jsonl.

The clips are TTS-voiced synthetic speech, so these are machine measurements on easy audio,
never an accuracy figure for patients (no Arabic accuracy percentage may be quoted).
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Final

from careflow_eval.speech import (
    LANGUAGES,
    Clip,
    Language,
    normalize,
    read_manifest,
    score,
    update_report,
)

REPO: Final = Path(__file__).resolve().parents[2]
MANIFEST: Final = REPO / "eval" / "datasets" / "speech" / "manifest.jsonl"
REPORT: Final = REPO / "eval" / "reports" / "speech.json"
TRANSCRIPTS: Final = REPO / "eval" / "reports" / "speech_transcripts.jsonl"
DOWNLOADS: Final = REPO / "services" / "voice" / "scripts" / "download_models.py"


def whisper_model() -> str:
    """The Whisper files the voice service uses, as org/model@revision (download_models.py)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("careflow_voice_downloads", DOWNLOADS)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {DOWNLOADS}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    [whisper] = {f"{f.hf_repo}@{f.hf_revision}" for f in module.FILES if f.whisper}
    return whisper


def read_audio(path: Path) -> Any:
    import wave

    import numpy as np

    with wave.open(str(path), "rb") as handle:
        if (handle.getframerate(), handle.getnchannels(), handle.getsampwidth()) != (16000, 1, 2):
            raise ValueError(f"{path}: expected 16 kHz mono PCM16")
        pcm = handle.readframes(handle.getnframes())
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


NUMBER_WORDS: Final = {
    word: str(value)
    for value, word in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
        "fifteen sixteen seventeen eighteen nineteen twenty".split()
    )
} | {"thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "hundred": "100"}


def digits_only(reference: str, transcript: str) -> bool:
    """English: the two differ only where a number word was written as digits ("7 out of 10")."""
    ref, hyp = normalize(reference, "en").split(), normalize(transcript, "en").split()
    if ref == hyp or len(ref) != len(hyp):
        return False
    return all(r == h or NUMBER_WORDS.get(r) == h for r, h in zip(ref, hyp, strict=True))


def set_of(clip: Clip) -> str:
    return "sentences" if clip.origin == "sentences" else "scenarios"


def rows_for(
    engine: str, model: str, clips: list[Clip], transcripts: dict[str, str]
) -> list[dict[str, Any]]:
    """One row per language, dialect and set, and one per language and set over its dialects."""
    rows: list[dict[str, Any]] = []
    present: list[Language] = [code for code in LANGUAGES if any(c.lang == code for c in clips)]
    for lang in present:
        for set_name in ("sentences", "scenarios"):
            in_set = [c for c in clips if c.lang == lang and set_of(c) == set_name]
            dialects = sorted({c.dialect for c in in_set})
            for dialect in [*dialects, *(["all"] if len(dialects) > 1 else [])]:
                picked = [c for c in in_set if dialect in ("all", c.dialect)]
                if not picked:
                    continue
                result = score([c.text for c in picked], [transcripts[c.id] for c in picked], lang)
                wrong = [
                    c
                    for c in picked
                    if normalize(c.text, lang) != normalize(transcripts[c.id], lang)
                ]
                extra: dict[str, int] = {"clips_with_errors": len(wrong)}
                if lang == "en":
                    extra["digits_only"] = sum(
                        digits_only(c.text, transcripts[c.id]) for c in wrong
                    )
                rows.append(
                    {
                        "provider": engine,
                        "model": model,
                        "lang": lang,
                        "dialect": dialect,
                        "set": set_name,
                        "n": len(picked),
                        **result,
                        **extra,
                    }
                )
    return rows


def not_run_rows(engine: str, model: str, clips: list[Clip], reason: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for set_name in ("sentences", "scenarios"):
        in_set = [c for c in clips if set_of(c) == set_name]
        for dialect in sorted({c.dialect for c in in_set}):
            rows.append(
                {
                    "provider": engine,
                    "model": model,
                    "lang": "ar",
                    "dialect": dialect,
                    "set": set_name,
                    "n": sum(c.dialect == dialect for c in in_set),
                    "wer": None,
                    "cer": None,
                    "not_run": reason,
                }
            )
    return rows


def transcribe_all(
    clips: list[Clip], transcriber: Callable[[str], Callable[[Any], str]]
) -> tuple[dict[str, str], float]:
    """Each distinct clip once (scenario turns share clips); the mean seconds per clip."""
    engines = {lang: transcriber(lang) for lang in {clip.lang for clip in clips}}
    heard: dict[str, str] = {}
    started = time.perf_counter()
    for clip in clips:
        if clip.audio_path not in heard:
            heard[clip.audio_path] = engines[clip.lang](read_audio(REPO / clip.audio_path)).strip()
    seconds = (time.perf_counter() - started) / max(1, len(heard))
    return {clip.id: heard[clip.audio_path] for clip in clips}, seconds


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    from voice_agent.config.careflow import CareflowSettings
    from voice_agent.providers.cohere_transcribe_stt import (
        MODEL_ID,
        MODEL_REVISION,
        CohereTranscribeUnavailableError,
        cohere_transcriber,
        ensure_accessible,
    )
    from voice_agent.providers.mlx_whisper_stt import mlx_transcriber

    clips = read_manifest(MANIFEST)
    settings = CareflowSettings()
    whisper, per_clip = transcribe_all(
        clips, lambda lang: mlx_transcriber(settings.mlx_whisper_model, lang)
    )
    rows = rows_for("mlx-whisper", whisper_model(), clips, whisper)
    records = [
        {
            "id": c.id,
            "lang": c.lang,
            "dialect": c.dialect,
            "provider": "mlx-whisper",
            "reference": c.text,
            "transcript": whisper[c.id],
        }
        for c in clips
    ]
    timings = {"mlx-whisper": round(per_clip, 3)}

    arabic = [clip for clip in clips if clip.lang == "ar"]
    cohere_model = f"{MODEL_ID}@{MODEL_REVISION}"
    try:
        ensure_accessible()
    except CohereTranscribeUnavailableError as exc:
        reason = str(exc)
        print(f"run_wer: cohere-transcribe-arabic not_run: {reason}")
        rows += not_run_rows("cohere-transcribe-arabic", cohere_model, arabic, reason)
    else:  # the owner unlocked it: the same clips, the same scoring
        cohere, per_clip = transcribe_all(arabic, lambda _lang: cohere_transcriber())
        rows += rows_for("cohere-transcribe-arabic", cohere_model, arabic, cohere)
        records += [
            {
                "id": c.id,
                "lang": c.lang,
                "dialect": c.dialect,
                "provider": "cohere-transcribe-arabic",
                "reference": c.text,
                "transcript": cohere[c.id],
            }
            for c in arabic
        ]
        timings["cohere-transcribe-arabic"] = round(per_clip, 3)

    TRANSCRIPTS.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
    )
    update_report(
        REPORT,
        "stt",
        {
            "method": (
                "each clip transcribed whole in its language; both sides normalised "
                "(careflow_eval.speech.normalize) and scored with jiwer (corpus WER and CER)"
            ),
            "seconds_per_clip": timings,
            "rows": rows,
        },
    )
    for row in rows:
        if row.get("dialect") in ("all", "en") or row["lang"] == "en":
            print(
                f"run_wer: {row['provider']} {row['lang']} {row['dialect']} {row['set']}: "
                f"n={row['n']} wer={row['wer']} cer={row['cer']}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
