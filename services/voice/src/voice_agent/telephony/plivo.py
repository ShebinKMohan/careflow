from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from xml.sax.saxutils import escape, quoteattr

PLIVO_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
DTMF_DIGITS = frozenset("0123456789*#ABCD")


class PlivoProtocolError(ValueError):
    """Raised when a Plivo WebSocket message is malformed."""


class PlivoAudioFormat(StrEnum):
    MULAW_8KHZ = "audio/x-mulaw;rate=8000"
    L16_8KHZ = "audio/x-l16;rate=8000"
    L16_16KHZ = "audio/x-l16;rate=16000"

    @property
    def content_type(self) -> str:
        return self.value.split(";", maxsplit=1)[0]

    @property
    def sample_rate(self) -> int:
        try:
            return int(self.value.rsplit("rate=", maxsplit=1)[1])
        except (IndexError, ValueError) as exc:
            raise PlivoProtocolError(f"Unsupported Plivo audio format: {self.value}") from exc


@dataclass(frozen=True, slots=True)
class PlivoInboundMessage:
    kind: str
    raw: dict[str, Any]
    audio: bytes | None = None
    digit: str | None = None
    stream_id: str | None = None
    # The checkpoint name echoed back on a "playedStream" event.
    name: str | None = None


def build_answer_xml(
    *,
    websocket_url: str,
    status_callback_url: str | None,
    status_callback_method: str,
    stream_timeout_seconds: int,
    audio_format: PlivoAudioFormat,
    noise_cancellation: bool,
    noise_cancellation_level: int,
) -> str:
    if not websocket_url.startswith("wss://"):
        raise PlivoProtocolError("Plivo media streams must use a publicly reachable wss:// URL.")
    if stream_timeout_seconds <= 0:
        raise PlivoProtocolError("stream_timeout_seconds must be positive.")
    if status_callback_method not in {"GET", "POST"}:
        raise PlivoProtocolError("status_callback_method must be GET or POST.")
    if not 60 <= noise_cancellation_level <= 100:
        raise PlivoProtocolError("noise_cancellation_level must be between 60 and 100.")

    attributes = [
        'bidirectional="true"',
        'keepCallAlive="true"',
        f"contentType={quoteattr(audio_format.value)}",
        f"streamTimeout={quoteattr(str(stream_timeout_seconds))}",
    ]
    if status_callback_url:
        attributes.append(f"statusCallbackUrl={quoteattr(status_callback_url)}")
        attributes.append(f"statusCallbackMethod={quoteattr(status_callback_method)}")
    if noise_cancellation:
        attributes.append('noiseCancellation="true"')
        attributes.append(f"noiseCancellationLevel={quoteattr(str(noise_cancellation_level))}")

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"<Stream {' '.join(attributes)}>{escape(websocket_url)}</Stream>"
        "</Response>"
    )


def build_hangup_xml(message: str | None = None) -> str:
    clean_message = message.strip() if message is not None else ""
    speak = f"<Speak>{escape(clean_message)}</Speak>" if clean_message else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"{speak}<Hangup/>"
        "</Response>"
    )


def play_audio_message(audio: bytes, audio_format: PlivoAudioFormat) -> dict[str, Any]:
    if not audio:
        raise PlivoProtocolError("Cannot send empty audio to Plivo.")
    _validate_l16_audio_frame(audio, audio_format)
    return {
        "event": "playAudio",
        "media": {
            "contentType": audio_format.content_type,
            "sampleRate": audio_format.sample_rate,
            "payload": base64.b64encode(audio).decode("ascii"),
        },
    }


def clear_audio_message() -> dict[str, str]:
    return {"event": "clearAudio"}


def checkpoint_message(name: str) -> dict[str, str]:
    """Ask Plivo to emit a 'playedStream' event (carrying this name) once all audio buffered
    before this point has finished playing — the authoritative playback-finished signal."""
    return {"event": "checkpoint", "name": name}


def parse_plivo_message(
    message: dict[str, Any],
    *,
    audio_format: PlivoAudioFormat | None = None,
) -> PlivoInboundMessage:
    event = message.get("event")
    if not isinstance(event, str):
        raise PlivoProtocolError("Plivo message is missing a string event field.")

    if event == "media":
        media = message.get("media")
        if not isinstance(media, dict):
            raise PlivoProtocolError("Plivo media message is missing media object.")
        payload = media.get("payload")
        if not isinstance(payload, str):
            raise PlivoProtocolError("Plivo media message is missing string payload.")
        try:
            audio = base64.b64decode(payload, validate=True)
        except ValueError as exc:
            raise PlivoProtocolError("Plivo media payload is not valid base64.") from exc
        if not audio:
            raise PlivoProtocolError("Plivo media payload decoded to empty audio.")
        _validate_l16_audio_frame(audio, audio_format)
        return PlivoInboundMessage(
            kind="media",
            raw=message,
            audio=audio,
            stream_id=_message_stream_id(message),
        )

    if event == "dtmf":
        dtmf = message.get("dtmf")
        if not isinstance(dtmf, dict):
            raise PlivoProtocolError("Plivo DTMF message is missing dtmf object.")
        digit = dtmf.get("digit")
        if not isinstance(digit, str) or len(digit) != 1 or digit not in DTMF_DIGITS:
            raise PlivoProtocolError("Plivo DTMF message must include a single DTMF digit.")
        return PlivoInboundMessage(
            kind="dtmf",
            raw=message,
            digit=digit,
            stream_id=_message_stream_id(message),
        )

    if event == "playedStream":
        name = message.get("name")
        return PlivoInboundMessage(
            kind="playedStream",
            raw=message,
            name=name.strip() if isinstance(name, str) and name.strip() else None,
            stream_id=_message_stream_id(message),
        )

    return PlivoInboundMessage(kind=event, raw=message, stream_id=_message_stream_id(message))


def extract_start_identifiers(message: dict[str, Any]) -> tuple[str, str]:
    start = message.get("start")
    if not isinstance(start, dict):
        raise PlivoProtocolError("Plivo start message is missing start metadata.")

    call_id = _first_string(
        start,
        "call_uuid",
        "callUuid",
        "call_id",
        "callId",
        "callSid",
        "call_sid",
    )
    stream_id = _first_string(
        start,
        "stream_id",
        "streamId",
        "stream_sid",
        "streamSid",
        "stream_uuid",
        "streamUuid",
    )
    if call_id is None or stream_id is None:
        raise PlivoProtocolError("Plivo start message must include call and stream identifiers.")
    _require_plivo_identifier("call", call_id)
    _require_plivo_identifier("stream", stream_id)
    return call_id, stream_id


def _first_string(source: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _require_plivo_identifier(name: str, value: str) -> None:
    if PLIVO_IDENTIFIER_RE.fullmatch(value) is None:
        raise PlivoProtocolError(
            f"Plivo start {name} identifier must be 1-128 characters using letters, "
            "numbers, dot, underscore, colon, or hyphen."
        )


def _validate_l16_audio_frame(
    audio: bytes,
    audio_format: PlivoAudioFormat | None,
) -> None:
    if audio_format in {PlivoAudioFormat.L16_8KHZ, PlivoAudioFormat.L16_16KHZ} and (
        len(audio) % 2
    ):
        raise PlivoProtocolError("Plivo 16-bit PCM media payload has an incomplete sample.")


def _message_stream_id(message: dict[str, Any]) -> str | None:
    return _first_string(
        message,
        "streamId",
        "stream_id",
        "streamSid",
        "stream_sid",
        "streamUuid",
        "stream_uuid",
    )
