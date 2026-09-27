"""`WS /kiosk/audio`: the kiosk's microphone in, transcripts, questions and speech out."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from voice_agent.config.settings import Settings
from voice_agent.kiosk.auth import KioskAuthError, PatientSession, verify_patient_token
from voice_agent.kiosk.loop import KioskTurnLoop
from voice_agent.kiosk.protocol import (
    CLOSE_POLICY_VIOLATION,
    CLOSE_SERVER_ERROR,
    MAX_FRAME_BYTES,
    StartMessage,
    StopMessage,
    error_message,
)
from voice_agent.kiosk.speech import KioskSpeech
from voice_agent.kiosk.turns import TurnPoster
from voice_agent.storage.call_events import CallEventStore

logger = logging.getLogger(__name__)

router = APIRouter()


@dataclass
class KioskChannel:
    """What every kiosk connection shares; stored on `app.state.kiosk`."""

    settings: Settings
    speech: KioskSpeech
    turns: TurnPoster
    events: CallEventStore | None = None


class _Sender:
    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket

    async def send_json(self, message: dict[str, Any]) -> None:
        await self._websocket.send_text(json.dumps(message, separators=(",", ":")))

    async def send_bytes(self, data: bytes) -> None:
        await self._websocket.send_bytes(data)


async def _receive_start(websocket: WebSocket) -> StartMessage | None:
    message = await websocket.receive()
    text = message.get("text")
    if not isinstance(text, str):
        return None
    try:
        return StartMessage.model_validate_json(text)
    except ValidationError:
        return None


@router.websocket("/kiosk/audio")
async def kiosk_audio(websocket: WebSocket) -> None:
    channel: KioskChannel = websocket.app.state.kiosk
    settings = channel.settings
    await websocket.accept()
    try:
        start = await asyncio.wait_for(
            _receive_start(websocket), timeout=settings.kiosk_start_timeout_seconds
        )
    except (TimeoutError, WebSocketDisconnect, RuntimeError):
        start = None
    if start is None:
        with suppress(Exception):
            await websocket.close(code=CLOSE_POLICY_VIOLATION)
        return
    secret = settings.careflow_jwt_secret
    if secret is None:
        logger.error("CAREFLOW_JWT_SECRET is not set: the kiosk voice channel cannot check tokens.")
        await websocket.close(code=CLOSE_SERVER_ERROR)
        return
    try:
        session: PatientSession = verify_patient_token(
            start.token, session_id=start.session_id, secret=secret.get_secret_value()
        )
    except KioskAuthError as exc:
        logger.info("Kiosk voice start refused: %s", exc)
        await websocket.close(code=CLOSE_POLICY_VIOLATION)
        return

    sender = _Sender(websocket)
    try:
        loop = KioskTurnLoop(
            session=session,
            stt=channel.speech.stt(start.language),
            tts=channel.speech.tts(start.language),
            vad=channel.speech.vad(),
            turns=channel.turns,
            sender=sender,
            events=channel.events,
            language=start.language,
        )
        await loop.start()
    except Exception:
        logger.exception("The kiosk speech engines are unavailable.")
        with suppress(Exception):
            await sender.send_json(error_message("speech_unavailable"))
            await websocket.close(code=CLOSE_SERVER_ERROR)
        return

    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            data = message.get("bytes")
            if isinstance(data, bytes):
                if len(data) > MAX_FRAME_BYTES or len(data) % 2:
                    await sender.send_json(error_message("bad_frame"))
                    continue
                await loop.feed(data)
                continue
            text = message.get("text")
            if isinstance(text, str):
                try:
                    StopMessage.model_validate_json(text)
                except ValidationError:
                    await sender.send_json(error_message("bad_message"))
                    continue
                break
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        await loop.stop()
        with suppress(Exception):
            await websocket.close()
