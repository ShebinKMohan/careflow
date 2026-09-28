"""The speech eval's shared pieces (M2-02): the clip manifest, the normalisation both sides of a
comparison get, WER and CER with jiwer, and nearest-rank percentiles.

- **Arabic.** Strip the harakat (U+064B to U+0652) and tatweel (U+0640), map أ, إ and آ to ا and
  ى to ي, and drop punctuation (Arabic ؟ ، ؛ included).
- **English.** Lowercase and drop punctuation.

Both collapse whitespace, and the same function runs on the reference and on the transcript, so
only what the speech engine got wrong counts. The clips are TTS-voiced synthetic sentences
(`eval/datasets/speech/`): the numbers are machine measurements on easy audio, never an accuracy
figure for real patients (CONVENTIONS: no Arabic accuracy percentage).
"""

from __future__ import annotations

import datetime as dt
import json
import math
import platform
import re
import shutil
import subprocess
import unicodedata
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Final, Literal

import jiwer
from pydantic import BaseModel, ConfigDict, Field

Language = Literal["en", "ar"]
LANGUAGES: Final[tuple[Language, ...]] = ("en", "ar")

_HARAKAT_AND_TATWEEL: Final = re.compile("[\u064b-\u0652\u0640]")
_ALEFS: Final = re.compile("[\u0623\u0625\u0622]")  # أ إ آ
CAVEATS: Final = (
    "The clips are TTS-voiced (Kokoro for English, MMS-TTS for Arabic), so the numbers are "
    "optimistic: synthetic speech is cleaner and more regular than a patient's.",
    "The sentences and scenario turns are synthetic and written by the agent; neither the text "
    "nor the dialect labels were reviewed by native speakers.",
    "These are machine measurements on one Mac, never an accuracy figure for real patients: no "
    "Arabic accuracy percentage may be quoted from them (CONVENTIONS).",
    "MMS-TTS Arabic (facebook/mms-tts-ara) is CC-BY-NC-4.0: research and non-commercial use only; "
    "a commercial release needs another Arabic voice (ADR-033).",
    "Cohere Transcribe Arabic is gated on Hugging Face; its rows are not_run until the owner "
    "accepts its terms with their own login.",
)


class Clip(BaseModel):
    """One row of `eval/datasets/speech/manifest.jsonl`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    lang: Language
    dialect: str = Field(min_length=1)  # "gulf" or "levantine" for Arabic, "en" for English
    source: Literal["tts"] = "tts"
    origin: str = Field(min_length=1)  # "sentences" or "scenario:<scenario id>#<turn>"
    text: str = Field(min_length=1)
    audio_path: str = Field(min_length=1)  # relative to the repository; the WAVs are gitignored
    voice: str = Field(min_length=1)  # the TTS engine as org/model@revision (and the voice)


def read_manifest(path: Path) -> list[Clip]:
    rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    clips = [Clip.model_validate_json(line) for line in rows]
    ids = [clip.id for clip in clips]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path}: duplicate clip ids")
    return clips


def write_manifest(clips: Iterable[Clip], path: Path) -> None:
    lines = [json.dumps(clip.model_dump(), ensure_ascii=False) for clip in clips]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _without_punctuation(text: str) -> str:
    return "".join(
        " " if unicodedata.category(char).startswith(("P", "S")) else char for char in text
    )


def normalize(text: str, lang: Language) -> str:
    """The comparison form of `text` (see the module docstring)."""
    if lang == "ar":
        text = _HARAKAT_AND_TATWEEL.sub("", text)
        text = _ALEFS.sub("\u0627", text).replace("\u0649", "\u064a")  # ى -> ي
    else:
        text = text.lower()
    return " ".join(_without_punctuation(text).split())


def score(references: Sequence[str], hypotheses: Sequence[str], lang: Language) -> dict[str, float]:
    """Corpus WER and CER of `hypotheses` against `references`, both normalised."""
    if len(references) != len(hypotheses) or not references:
        raise ValueError("score needs as many transcripts as references, and at least one")
    refs = [normalize(text, lang) for text in references]
    hyps = [normalize(text, lang) for text in hypotheses]
    if any(not ref for ref in refs):
        raise ValueError("a reference is empty after normalisation")
    return {
        "wer": round(float(jiwer.wer(refs, hyps)), 4),
        "cer": round(float(jiwer.cer(refs, hyps)), 4),
    }


def machine() -> dict[str, Any]:
    """This Mac, without a user name or a path."""
    info: dict[str, Any] = {"arch": platform.machine(), "macos": platform.mac_ver()[0] or None}
    sysctl = shutil.which("sysctl")
    if sysctl is not None:
        for key, name in (("machdep.cpu.brand_string", "chip"), ("hw.memsize", "memory_gb")):
            result = subprocess.run(  # noqa: S603 - a fixed read-only query, no shell
                [sysctl, "-n", key], capture_output=True, text=True, check=False
            )
            value = result.stdout.strip()
            if value:
                info[name] = round(int(value) / 2**30) if name == "memory_gb" else value
    return info


def update_report(path: Path, section: str, data: dict[str, Any]) -> dict[str, Any]:
    """Write one section of eval/reports/speech.json, keeping the others."""
    report: dict[str, Any] = {}
    if path.is_file():
        report = json.loads(path.read_text(encoding="utf-8"))
    now = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    report.update(
        {
            "generated_at": now,
            "machine": machine(),
            "publish": False,  # M6-01 decides what is published; never an accuracy headline
            "caveats": list(CAVEATS),
        }
    )
    report[section] = {"generated_at": now, **data}
    order = ["generated_at", "machine", "publish", "caveats", "clips", "stt", "turns", "latency"]
    ordered = {key: report[key] for key in order if key in report}
    ordered.update({key: value for key, value in report.items() if key not in ordered})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ordered, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return ordered


def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile, the method of the voice service's latency summary."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * q) - 1)]


__all__ = [
    "CAVEATS",
    "LANGUAGES",
    "Clip",
    "Language",
    "machine",
    "normalize",
    "percentile",
    "read_manifest",
    "score",
    "update_report",
    "write_manifest",
]
