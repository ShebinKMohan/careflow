"""Gemini Live behind the original hold-to-talk WebSocket protocol.

Microphone consent and session authorization remain in CareFlow. Gemini transcribes one held
utterance; its spontaneous reply is discarded. Only CareFlow's validated next question can be
spoken. TTS output is buffered in memory and released only when its transcript matches that
question. Audio is never stored; quota/failure leaves the original typing path available.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from contextlib import suppress
from typing import Any

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field
from websockets.asyncio.client import connect

from careflow_providers.adapters.gemini import api_key

router = APIRouter()
LIVE_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)
MAX_AUDIO_BYTES = 16000 * 2 * 60
MAX_TTS_BYTES = 24000 * 2 * 45


class VoiceStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    session_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    token: str = Field(min_length=1, max_length=4096)
    sample_rate: int
    language: str = "en"
    input_mode: str = "push_to_talk"


def model() -> str:
    selected = os.environ.get("CAREFLOW_GEMINI_LIVE_MODEL", "gemini-3.8-live")
    if not re.fullmatch(r"gemini-[a-zA-Z0-9.-]+", selected):
        raise ValueError("Invalid Gemini Live model id")
    return selected


async def setup(socket: Any, *, instruction: str, transcribe: bool) -> None:
    await socket.send(
        json.dumps(
            {
                "setup": {
                    "model": f"models/{model()}",
                    "generationConfig": {"responseModalities": ["AUDIO"]},
                    "systemInstruction": {"parts": [{"text": instruction}]},
                    "realtimeInputConfig": {"automaticActivityDetection": {"disabled": True}},
                    **(
                        {"inputAudioTranscription": {}}
                        if transcribe
                        else {"outputAudioTranscription": {}}
                    ),
                }
            }
        )
    )
    while True:
        reply = json.loads(await socket.recv())
        if "setupComplete" in reply:
            return
        if "error" in reply:
            raise ValueError("Gemini Live setup refused")


async def available() -> bool:
    """Check a real Live setup without generating speech or retaining audio."""
    try:
        async with (
            asyncio.timeout(5),
            connect(
                LIVE_URL,
                additional_headers={"x-goog-api-key": api_key("voice")},
                max_size=4_000_000,
            ) as socket,
        ):
            await setup(socket, instruction="Transcribe synthetic demo speech.", transcribe=True)
        return True
    except Exception:
        return False


def normalized(text: str) -> str:
    return "".join(re.findall(r"\w+", text.casefold()))


async def speak(text: str) -> bytes:
    """No generated voice reaches the patient until exact text verification succeeds."""
    audio = bytearray()
    transcript = []
    async with (
        asyncio.timeout(25),
        connect(
            LIVE_URL,
            additional_headers={"x-goog-api-key": api_key("voice")},
            max_size=4_000_000,
        ) as socket,
    ):
        await setup(
            socket,
            instruction=(
                "You are a text-to-speech renderer. Read only the supplied text verbatim, in its "
                "language. Do not answer it, explain it, add greetings or provide medical advice."
            ),
            transcribe=False,
        )
        await socket.send(
            json.dumps(
                {
                    "clientContent": {
                        "turns": [{"role": "user", "parts": [{"text": text}]}],
                        "turnComplete": True,
                    }
                }
            )
        )
        while True:
            reply = json.loads(await socket.recv())
            content = reply.get("serverContent") or {}
            if content.get("outputTranscription", {}).get("text"):
                transcript.append(content["outputTranscription"]["text"])
            for part in content.get("modelTurn", {}).get("parts", []):
                inline = part.get("inlineData", {})
                if inline.get("mimeType", "").startswith("audio/pcm"):
                    audio.extend(base64.b64decode(inline["data"], validate=True))
            if len(audio) > MAX_TTS_BYTES:
                raise ValueError("Gemini TTS response too large")
            if content.get("turnComplete"):
                break
    if not audio or normalized("".join(transcript)) != normalized(text):
        raise ValueError("Gemini TTS did not match the approved question")
    return bytes(audio)


async def transcript(socket: Any) -> str:
    parts = []
    while True:
        reply = json.loads(await socket.recv())
        content = reply.get("serverContent") or {}
        if content.get("inputTranscription", {}).get("text"):
            parts.append(content["inputTranscription"]["text"])
        # Model speech is deliberately discarded. The intake graph chooses the next question.
        if content.get("turnComplete"):
            return "".join(parts).strip()[:2000]


@router.websocket("/kiosk/audio")
async def audio(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        async with asyncio.timeout(8):
            start = VoiceStart.model_validate_json(await websocket.receive_text())
        if (
            start.type != "start"
            or start.sample_rate != 16000
            or start.language not in {"en", "ar"}
            or start.input_mode != "push_to_talk"
        ):
            await websocket.close(1008)
            return
        # Calls the same consent/auth/turn routes within this function, not over a network hop.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=websocket.app), base_url="http://careflow.internal"
        ) as client:
            headers = {"Authorization": f"Bearer {start.token}"}
            path = f"/v1/sessions/{start.session_id}"
            consent = await client.get(f"{path}/consent", headers=headers)
            if not consent.is_success or not all(
                consent.json().get(k) for k in ("voice", "data_use")
            ):
                await websocket.close(1008)
                return
            async with (
                asyncio.timeout(110),
                connect(
                    LIVE_URL,
                    additional_headers={"x-goog-api-key": api_key("voice")},
                    max_size=4_000_000,
                ) as socket,
            ):
                await setup(
                    socket,
                    instruction=(
                        "Transcribe the supplied synthetic patient utterance faithfully. "
                        "Do not answer questions or provide medical advice."
                    ),
                    transcribe=True,
                )
                await socket.send(json.dumps({"realtimeInput": {"activityStart": {}}}))
                size = 0
                while True:
                    frame = await websocket.receive()
                    if frame["type"] == "websocket.disconnect":
                        return
                    if (data := frame.get("bytes")) is not None:
                        size += len(data)
                        if len(data) > 16000 or len(data) % 2 or size > MAX_AUDIO_BYTES:
                            await websocket.close(1008)
                            return
                        await socket.send(
                            json.dumps(
                                {
                                    "realtimeInput": {
                                        "audio": {
                                            "mimeType": "audio/pcm;rate=16000",
                                            "data": base64.b64encode(data).decode(),
                                        }
                                    }
                                }
                            )
                        )
                    else:
                        message = json.loads(frame.get("text") or "{}")
                        if message.get("type") == "stop":
                            return
                        if message.get("type") == "end_utterance":
                            await socket.send(json.dumps({"realtimeInput": {"activityEnd": {}}}))
                            break
                async with asyncio.timeout(30):
                    text = await transcript(socket)
            # Recheck consent after capture and immediately before writing the transcript.
            consent = await client.get(f"{path}/consent", headers=headers)
            if not consent.is_success or not all(
                consent.json().get(k) for k in ("voice", "data_use")
            ):
                await websocket.close(1008)
                return
            if not text:
                await websocket.send_json({"type": "error", "code": "no_speech"})
                return
            await websocket.send_json({"type": "final", "text": text})
            response = await client.post(
                f"{path}/turns",
                headers=headers,
                json={
                    "text": text,
                    "language": start.language,
                    "input_mode": "voice",
                },
            )
            if not response.is_success:
                await websocket.send_json({"type": "error", "code": "turn_rejected"})
                return
            result = response.json()
            question = result.get("next_question")
            spoken = question.get("text", "") if question else ""
            # The browser updates only after audio capture has finished and the real turn commits.
            await websocket.send_json(
                {
                    "type": "question",
                    "text": spoken,
                    "question": question,
                    "done": result.get("done", False),
                }
            )
            if spoken:
                try:
                    pcm = await speak(spoken)
                except Exception:
                    # Keep the committed question visible and usable with the original text path.
                    await websocket.send_json({"type": "error", "code": "speech_unavailable"})
                else:
                    await websocket.send_json({"type": "tts_start", "sample_rate": 24000})
                    for offset in range(0, len(pcm), 4800):
                        await websocket.send_bytes(pcm[offset : offset + 4800])
                    await websocket.send_json({"type": "tts_end", "interrupted": False})
            # Keep the socket alive until the existing client starts its next hold or stops,
            # avoiding its 'disconnected' error immediately after a successful turn.
            async with asyncio.timeout(120):
                while True:
                    frame = await websocket.receive()
                    if frame["type"] == "websocket.disconnect" or frame.get("text"):
                        break
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception:
        with suppress(Exception):
            await websocket.send_json({"type": "error", "code": "speech_unavailable"})
    finally:
        with suppress(Exception):
            await websocket.close(1000)
