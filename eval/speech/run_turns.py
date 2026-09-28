"""How often the kiosk's end-of-turn detection cuts one spoken answer in two (M2-02).

    uv run --extra arabic-speech python eval/speech/run_turns.py [--grid]

Every clip of eval/datasets/speech/manifest.jsonl is one answer. It is fed through the voice
service's own turn logic (`MlxWhisperSTT`: the real Silero VAD and Smart Turn v3, the turn
tracker) with a stub transcriber, so only turn-taking is measured, never Whisper. A clip that
becomes more than one turn is a **split**: the kiosk would post the first words as the whole
answer. It is counted for:

- `configured`: the settings the voice service uses for the language (config/providers.yaml
  `speech.turn`, else the service defaults);
- `m1-14`: the service's settings before M2-02 (Smart Turn at 0.5 after 200 ms of silence, a
  400 ms silence-only end, a 900 ms hard end), for comparison, on today's code (which counts no
  frame under -60 dBFS as speech);
- with `--grid`, the candidates ADR-058 chose the configured settings from.

A turn whose audio is near-silent (RMS under 0.005, about -46 dBFS; the clips' speech is around
-20 dBFS) is a **phantom**: the VAD fired on the quiet noise around the answer. Whisper turns
silence into text ("Thank you.", "ترجمة نانسي قنقر"), so on the kiosk a phantom turn would post
words the patient never said. Phantoms are counted apart from splits.

It writes the `turns` section of eval/reports/speech.json. TTS-voiced clips pause differently from
patients, so these rates only compare settings; they are not a patient's experience.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from careflow_eval.speech import Clip, read_manifest, update_report

REPO: Final = Path(__file__).resolve().parents[2]
MANIFEST: Final = REPO / "eval" / "datasets" / "speech" / "manifest.jsonl"
REPORT: Final = REPO / "eval" / "reports" / "speech.json"
FRAME_BYTES: Final = 640  # 20 ms of 16 kHz PCM16, as the kiosk sends it
PHANTOM_RMS: Final = 0.005  # a turn quieter than this holds no speech

M1_14: Final = {
    "smart_turn": True,
    "smart_turn_threshold": 0.5,
    "semantic_check_silence_ms": 200,
    "endpoint_silence_ms": 400,
    "hard_silence_ms": 900,
}
GRID: Final = {
    "smart-turn 0.9": {**M1_14, "smart_turn_threshold": 0.9},
    "silence 400 ms": {"smart_turn": False, "endpoint_silence_ms": 400, "hard_silence_ms": 900},
    "silence 550 ms": {"smart_turn": False, "endpoint_silence_ms": 550, "hard_silence_ms": 900},
    "silence 700 ms": {"smart_turn": False, "endpoint_silence_ms": 700, "hard_silence_ms": 1200},
    "silence 850 ms": {"smart_turn": False, "endpoint_silence_ms": 850, "hard_silence_ms": 1200},
}


def configured(lang: str) -> dict[str, Any]:
    """The voice service's turn settings for `lang`, as the kiosk builds them."""
    from voice_agent.config.careflow import CareflowSettings, load_speech_config

    turn = load_speech_config().turn_settings(lang, CareflowSettings())
    return {
        "smart_turn": turn.smart_turn_model_path is not None,
        "smart_turn_threshold": turn.smart_turn_threshold,
        "semantic_check_silence_ms": turn.semantic_check_silence_ms,
        "endpoint_silence_ms": turn.endpoint_silence_ms,
        "hard_silence_ms": turn.hard_silence_ms,
    }


class TurnCounter:
    """The kiosk's STT turn logic with a stub transcriber: how many turns each clip becomes."""

    def __init__(self) -> None:
        from voice_agent.config.careflow import CareflowSettings
        from voice_agent.providers._silero_vad import SileroVoiceActivityDetector

        self.settings = CareflowSettings()
        self.vad = SileroVoiceActivityDetector(model_path=self.settings.sherpa_stt_vad_model_path)

    async def turns(self, pcm: bytes, config: dict[str, Any]) -> list[float]:
        """The RMS of each turn the clip becomes."""
        from voice_agent.providers.mlx_whisper_stt import MlxWhisperSTT
        from voice_agent.providers.smart_turn import SmartTurnSemanticEndpointer

        semantic = None
        if config["smart_turn"]:
            semantic = SmartTurnSemanticEndpointer(
                model_path=self.settings.sherpa_stt_smart_turn_model_path or "",
                threshold=config["smart_turn_threshold"],
            )
        endpoint = config["endpoint_silence_ms"]
        levels: list[float] = []

        def transcribe(audio: Any) -> str:
            import numpy as np

            levels.append(float(np.sqrt(np.mean(np.square(audio)))) if len(audio) else 0.0)
            return "turn"

        stt = MlxWhisperSTT(
            vad_model_path="unused",
            vad_factory=lambda: self.vad,  # one Silero session, reset at each clip
            transcriber=transcribe,
            semantic_endpointer=semantic,
            semantic_check_silence_ms=config.get("semantic_check_silence_ms", min(200, endpoint)),
            endpoint_silence_ms=endpoint,
            hard_silence_ms=config["hard_silence_ms"],
        )
        await stt.start()
        for offset in range(0, len(pcm), FRAME_BYTES):
            await stt.accept_audio(pcm[offset : offset + FRAME_BYTES])
        await stt.close()  # the worker drains every queued frame first
        return levels


def read_pcm(path: Path) -> bytes:
    import wave

    with wave.open(str(path), "rb") as handle:
        if (handle.getframerate(), handle.getnchannels(), handle.getsampwidth()) != (16000, 1, 2):
            raise ValueError(f"{path}: expected 16 kHz mono PCM16")
        return handle.readframes(handle.getnframes())


async def measure(
    clips: list[Clip], configs: dict[str, dict[str, dict[str, Any]]]
) -> list[dict[str, Any]]:
    counter = TurnCounter()
    audio = {clip.audio_path: read_pcm(REPO / clip.audio_path) for clip in clips}
    rows: list[dict[str, Any]] = []
    for lang in ("en", "ar"):
        lang_clips = [clip for clip in clips if clip.lang == lang]
        for name, config in configs[lang].items():
            levels = [await counter.turns(audio[clip.audio_path], config) for clip in lang_clips]
            speech = [sum(level >= PHANTOM_RMS for level in turns) for turns in levels]
            phantom = [sum(level < PHANTOM_RMS for level in turns) for turns in levels]
            for dialect in sorted({clip.dialect for clip in lang_clips}) + ["all"]:
                if dialect != "all" and len({c.dialect for c in lang_clips}) == 1:
                    continue  # English has one "dialect": its "all" row says it
                picked = [
                    i for i, clip in enumerate(lang_clips) if dialect in ("all", clip.dialect)
                ]
                rows.append(
                    {
                        "lang": lang,
                        "dialect": dialect,
                        "settings": name,
                        **config,
                        "n": len(picked),
                        "split": sum(speech[i] > 1 for i in picked),
                        "phantom": sum(phantom[i] > 0 for i in picked),
                        "no_turn": sum(speech[i] == 0 for i in picked),
                        "split_clips": [lang_clips[i].id for i in picked if speech[i] > 1][:20],
                        "phantom_clips": [lang_clips[i].id for i in picked if phantom[i] > 0][:20],
                    }
                )
            print(
                f"run_turns: {lang} {name}: {sum(n > 1 for n in speech)}/{len(speech)} split, "
                f"{sum(n > 0 for n in phantom)} with a phantom turn, "
                f"{sum(n == 0 for n in speech)} unheard"
            )
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--grid", action="store_true", help="also the candidate settings")
    args = parser.parse_args(argv)
    clips = read_manifest(MANIFEST)
    configs = {
        lang: {"configured": configured(lang), "m1-14": dict(M1_14), **(GRID if args.grid else {})}
        for lang in ("en", "ar")
    }
    rows = asyncio.run(measure(clips, configs))
    update_report(
        REPORT,
        "turns",
        {
            "method": (
                "each clip is one answer, fed in 20 ms frames through the voice service's "
                "Silero VAD, Smart Turn v3 and turn tracker with a stub transcriber; a clip "
                "that becomes more than one turn is a split"
            ),
            "rows": rows,
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
