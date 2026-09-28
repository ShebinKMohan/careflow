"""`WS /kiosk/audio` messages.

Client → server:
- first `{"type": "start", "session_id", "token", "sample_rate": 16000}` (optional `language`);
- then binary PCM16 little-endian mono 16 kHz frames of 20 ms (640 bytes);
- optional `input_mode: "push_to_talk"` buffers until `{"type": "end_utterance"}`;
- `{"type": "stop"}` to end.

Server → client:
- `{"type": "partial" | "final", "text"}` (mlx-whisper sends finals only);
- `{"type": "question", "text", "question", "done"}`: `text` is what is spoken, `question` the
  API's `next_question` (IntakeQuestion or null), `done` true when the intake has no more
  questions;
- `{"type": "tts_start", "sample_rate": 24000}`, binary PCM16 frames, then
  `{"type": "tts_end", "interrupted": bool}` (`interrupted` after a barge-in: drop queued audio);
- `{"type": "error", "code"}`.
"""

from __future__ import annotations

from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

INPUT_SAMPLE_RATE: Final = 16000
OUTPUT_SAMPLE_RATE: Final = 24000
FRAME_BYTES: Final = 640  # 20 ms of PCM16 at 16 kHz
MAX_FRAME_BYTES: Final = 16000  # 0.5 s: anything larger is refused
TTS_FRAME_BYTES: Final = 4800  # 100 ms of PCM16 at 24 kHz

CLOSE_POLICY_VIOLATION: Final = 1008
CLOSE_SERVER_ERROR: Final = 1011


class StartMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["start"]
    session_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    token: str = Field(min_length=1, max_length=4096)
    sample_rate: Literal[16000]
    input_mode: Literal["continuous", "push_to_talk"] = "continuous"
    language: Literal["en", "ar"] = "en"  # the session's language (M2-01), spoken since M2-02


class StopMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["stop"]


class EndUtteranceMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["end_utterance"]


def text_message(kind: Literal["partial", "final"], text: str) -> dict[str, Any]:
    return {"type": kind, "text": text}


def question_message(text: str, question: dict[str, Any] | None, *, done: bool) -> dict[str, Any]:
    return {"type": "question", "text": text, "question": question, "done": done}


def tts_start_message() -> dict[str, Any]:
    return {"type": "tts_start", "sample_rate": OUTPUT_SAMPLE_RATE}


def tts_end_message(*, interrupted: bool) -> dict[str, Any]:
    return {"type": "tts_end", "interrupted": interrupted}


def error_message(code: str) -> dict[str, Any]:
    return {"type": "error", "code": code}
