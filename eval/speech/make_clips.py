"""Voice the speech eval's sentences and write eval/datasets/speech/manifest.jsonl (M2-02).

    uv run --extra arabic-speech python eval/speech/make_clips.py [--from-scenarios] [--force]

- **The engines.** English with Kokoro (the voice service's voice), Arabic with MMS-TTS
  (`facebook/mms-tts-ara`, the voice service's Arabic voice; CC-BY-NC-4.0). Both come from the
  model files `services/voice/scripts/download_models.sh` fetched; nothing is downloaded here.
- **The clips.** 16 kHz mono PCM16, with 0.5 s of silence before and 1.5 s after, like a kiosk
  turn. MMS samples its durations and noise, so each clip is seeded from its text: a rerun gives
  the same audio. A text said in several scenarios is voiced once.
- **The sentences.** `eval/datasets/speech/sentences.yaml`: 15 Gulf and 15 Levantine Arabic and
  15 English intake sentences.
- **`--from-scenarios`** also voices every patient turn of the scripted scenarios:
  `eval/datasets/intake_en` (M1-18, Kokoro) and `eval/datasets/intake_ar` (M2-03, MMS).

The WAVs go to `eval/datasets/speech/clips/` (gitignored); the manifest is committed. Synthetic
speech only: machine-voiced audio is easier than a patient's voice, so every number measured on
these clips is optimistic.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
import wave
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

import yaml

from careflow_eval.speech import Clip, Language, update_report, write_manifest

REPO: Final = Path(__file__).resolve().parents[2]
DATASET: Final = REPO / "eval" / "datasets" / "speech"
SENTENCES: Final = DATASET / "sentences.yaml"
MANIFEST: Final = DATASET / "manifest.jsonl"
REPORT: Final = REPO / "eval" / "reports" / "speech.json"
CLIPS: Final = DATASET / "clips"
SCENARIOS: Final = {
    "en": REPO / "eval" / "datasets" / "intake_en",
    "ar": REPO / "eval" / "datasets" / "intake_ar",
}
MODEL_HASHES: Final = REPO / "services" / "voice" / "models.sha256"
RATE: Final = 16000
LEAD_SECONDS: Final = 0.5
TAIL_SECONDS: Final = 1.5


def sentence_rows(path: Path = SENTENCES) -> list[dict[str, str]]:
    data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("synthetic") is not True:
        raise ValueError(f"{path}: expected `synthetic: true` and a `sentences` list")
    return [
        {
            "id": row["id"],
            "lang": row["lang"],
            "dialect": row["dialect"],
            "origin": "sentences",
            "text": row["text"],
        }
        for row in data["sentences"]
    ]


def scenario_rows(folders: dict[str, Path] = SCENARIOS) -> list[dict[str, str]]:
    """Every patient turn of the scripted scenarios, in file and turn order."""
    rows: list[dict[str, str]] = []
    for lang, folder in folders.items():
        for path in sorted(folder.glob("*.yaml")):
            scenario: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(scenario, dict) or scenario.get("synthetic") is not True:
                continue  # only the synthetic scenarios, never anything else in the folder
            dialect = str(scenario.get("dialect") or lang)
            for number, turn in enumerate(scenario.get("turns") or [], start=1):
                text = turn.get("patient_text") if isinstance(turn, dict) else None
                if not text:
                    continue
                rows.append(
                    {
                        "id": f"scn-{scenario['id']}-t{number}",
                        "lang": lang,
                        "dialect": dialect,
                        "origin": f"scenario:{scenario['id']}#{number}",
                        "text": str(text),
                    }
                )
    return rows


def clip_path(lang: str, text: str) -> Path:
    """One WAV per language and text, named by its hash (the text, not a patient, names it)."""
    digest = hashlib.sha256(f"{lang}\0{text}".encode()).hexdigest()
    return CLIPS / lang / f"{digest[:16]}.wav"


def seed_for(lang: str, text: str) -> int:
    return int(hashlib.sha256(f"seed\0{lang}\0{text}".encode()).hexdigest()[:8], 16)


def model_hash(name: str, path: Path = MODEL_HASHES) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == name:
            return parts[0]
    raise ValueError(f"{name} is not in {path}; run services/voice/scripts/download_models.sh")


def write_wav(path: Path, pcm: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(pcm)


class Voices:
    """The voice service's own TTS engines, at 16 kHz."""

    def __init__(self) -> None:
        from voice_agent.config.careflow import CareflowSettings
        from voice_agent.providers.kokoro_tts import KokoroTextToSpeech
        from voice_agent.providers.mms_tts import MODEL_ID, MODEL_REVISION, MmsTextToSpeech

        settings = CareflowSettings()
        self.kokoro = KokoroTextToSpeech(
            model_path=settings.kokoro_model_path,
            voices_path=settings.kokoro_voices_path,
            voice=settings.kokoro_voice,
            speed=settings.kokoro_speed,
            lang=settings.kokoro_lang,
            first_unit_max_chars=settings.kokoro_first_unit_max_chars,
        )
        self.mms = MmsTextToSpeech(model_path=settings.mms_tts_model_path)
        self.names: dict[Language, str] = {
            "en": (
                f"kokoro-v1.0.onnx@sha256:{model_hash('kokoro-v1.0.onnx')[:12]}, "
                f"voice {settings.kokoro_voice}, speed {settings.kokoro_speed}"
            ),
            "ar": f"{MODEL_ID}@{MODEL_REVISION}",
        }

    async def start(self) -> None:
        await self.kokoro.start()
        await self.mms.start()

    async def pcm16(self, lang: Language, text: str) -> bytes:
        from voice_agent.telephony.plivo import PlivoAudioFormat

        engine: Any = self.kokoro
        if lang == "ar":
            import torch  # type: ignore[import-not-found]

            torch.manual_seed(seed_for(lang, text))  # MMS samples: seeded, so reruns match
            engine = self.mms
        chunks = [
            chunk
            async for chunk in engine.synthesize(text=text, audio_format=PlivoAudioFormat.L16_16KHZ)
        ]
        lead = bytes(int(LEAD_SECONDS * RATE) * 2)
        tail = bytes(int(TAIL_SECONDS * RATE) * 2)
        return lead + b"".join(chunks) + tail


async def voice_all(rows: list[dict[str, str]], *, force: bool) -> list[Clip]:
    voices = Voices()
    await voices.start()
    clips: list[Clip] = []
    made = 0
    for row in rows:
        lang: Language = row["lang"]  # type: ignore[assignment]
        path = clip_path(lang, row["text"])
        if force or not path.is_file():
            write_wav(path, await voices.pcm16(lang, row["text"]))
            made += 1
        clips.append(
            Clip(
                id=row["id"],
                lang=lang,
                dialect=row["dialect"],
                origin=row["origin"],
                text=row["text"],
                audio_path=str(path.relative_to(REPO)),
                voice=voices.names[lang],
            )
        )
    print(f"make_clips: {made} clips voiced, {len(rows) - made} already there")
    return clips


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--from-scenarios", action="store_true", help="also every scenario turn")
    parser.add_argument("--force", action="store_true", help="re-voice clips that exist")
    args = parser.parse_args(argv)
    rows = sentence_rows() + (scenario_rows() if args.from_scenarios else [])
    clips = asyncio.run(voice_all(rows, force=args.force))
    write_manifest(clips, MANIFEST)
    by_lang = {lang: sum(clip.lang == lang for clip in clips) for lang in ("en", "ar")}
    groups: dict[str, int] = {}
    for clip in clips:
        kind = "sentences" if clip.origin == "sentences" else "scenarios"
        key = f"{clip.lang}/{clip.dialect}/{kind}"
        groups[key] = groups.get(key, 0) + 1
    update_report(
        REPORT,
        "clips",
        {
            "manifest": str(MANIFEST.relative_to(REPO)),
            "rows": len(clips),
            "distinct_audio": len({clip.audio_path for clip in clips}),
            "by_group": dict(sorted(groups.items())),
            "voices": dict(sorted({(clip.lang, clip.voice) for clip in clips})),
            "format": (
                f"16 kHz mono PCM16, {LEAD_SECONDS} s of silence before, {TAIL_SECONDS} s after"
            ),
        },
    )
    print(f"make_clips: {len(clips)} rows ({by_lang}) -> {MANIFEST.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
