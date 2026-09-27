from __future__ import annotations

import asyncio
import base64
import json
import logging
import ssl
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

import certifi
import httpx
import websockets

from voice_agent.providers._retry import compute_backoff, stream_with_retry
from voice_agent.telephony.plivo import PlivoAudioFormat

logger = logging.getLogger(__name__)


class SarvamTextToSpeechError(RuntimeError):
    """Raised when Sarvam returns an unusable text-to-speech response."""


DEFAULT_MAX_AUDIO_BYTES = 512 * 1024
DEFAULT_MODEL = "bulbul:v3"
DEFAULT_TARGET_LANGUAGE = "en-IN"


class SarvamTextToSpeech:
    """Sarvam Bulbul TTS adapter producing PCM16 audio for Plivo streaming.

    Uses the REST `text-to-speech` endpoint with `output_audio_codec=linear16`
    and `speech_sample_rate=16000`, which returns raw little-endian PCM16 that
    drops straight into the Plivo L16/16kHz media stream. Indian English is a
    first-class locale for Bulbul, so `target_language_code` defaults to en-IN.
    """

    def __init__(
        self,
        *,
        api_key: str,
        speaker: str | None = None,
        model: str = DEFAULT_MODEL,
        target_language_code: str = DEFAULT_TARGET_LANGUAGE,
        pace: float = 1.0,
        temperature: float = 0.6,
        enable_preprocessing: bool = True,
        api_base_url: str = "https://api.sarvam.ai",
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30.0,
        read_timeout_seconds: float | None = None,
        max_audio_bytes: int = DEFAULT_MAX_AUDIO_BYTES,
        retry_max_attempts: int = 1,
        retry_backoff_seconds: float = 0.25,
        retry_max_backoff_seconds: float = 2.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        if read_timeout_seconds is not None and read_timeout_seconds <= 0:
            raise ValueError("read_timeout_seconds must be positive.")
        if max_audio_bytes <= 0:
            raise ValueError("max_audio_bytes must be positive.")
        if pace <= 0:
            raise ValueError("pace must be positive.")
        if temperature <= 0:
            raise ValueError("temperature must be positive.")
        if retry_max_attempts < 1:
            raise ValueError("retry_max_attempts must be at least 1.")
        self._retry_max_attempts = retry_max_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._retry_max_backoff_seconds = retry_max_backoff_seconds
        self._api_key = _required_config_string(api_key, "api_key")
        # Sarvam speaker ids are lowercase (e.g. "anushka", "shubh"); its own model
        # docs list them capitalized, which the API rejects with 400. Normalize so a
        # copied-from-docs "Anushka" still works.
        self._speaker = (
            speaker.strip().lower() if isinstance(speaker, str) and speaker.strip() else None
        )
        self._model = _required_config_string(model, "model")
        self._target_language_code = _required_config_string(
            target_language_code, "target_language_code"
        )
        self._pace = pace
        self._temperature = temperature
        self._enable_preprocessing = enable_preprocessing
        self._endpoint = (
            _required_config_string(api_base_url, "api_base_url").rstrip("/") + "/text-to-speech"
        )
        self._timeout_seconds = timeout_seconds
        # Bound the read phase tighter than the overall timeout: on a live call a hung
        # TTS endpoint must fail fast (the caller hears silence) instead of stalling for
        # the full timeout. Default to <=10s of inter-chunk wait.
        read_timeout = (
            min(timeout_seconds, 10.0) if read_timeout_seconds is None else read_timeout_seconds
        )
        self._read_timeout_seconds = read_timeout
        self._max_audio_bytes = max_audio_bytes
        # Base64 inflates ~1.34x and the audio is wrapped in JSON, so allow headroom.
        self._max_response_bytes = max_audio_bytes * 2 + 64 * 1024
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, read=read_timeout)
        )
        self._owns_client = client is None

    async def synthesize(
        self,
        *,
        text: str,
        audio_format: PlivoAudioFormat,
    ) -> AsyncIterator[bytes]:
        if audio_format is not PlivoAudioFormat.L16_16KHZ:
            raise ValueError("Sarvam adapter is configured for PCM16 16kHz Plivo streams.")
        clean_text = text.strip()
        if not clean_text:
            raise SarvamTextToSpeechError("Sarvam text must not be blank.")

        payload: dict[str, object] = {
            "text": clean_text,
            "target_language_code": self._target_language_code,
            "model": self._model,
            "speech_sample_rate": audio_format.sample_rate,
            "output_audio_codec": "linear16",
            "pace": self._pace,
            "enable_preprocessing": self._enable_preprocessing,
        }
        if self._speaker is not None:
            payload["speaker"] = self._speaker
        # temperature is a bulbul:v3-only control (pitch/loudness are ignored on v3).
        if "v3" in self._model:
            payload["temperature"] = self._temperature

        async with stream_with_retry(
            self._client,
            "POST",
            self._endpoint,
            headers={
                "api-subscription-key": self._api_key,
                "content-type": "application/json",
            },
            json=payload,
            max_attempts=self._retry_max_attempts,
            backoff_seconds=self._retry_backoff_seconds,
            max_backoff_seconds=self._retry_max_backoff_seconds,
        ) as response:
            response.raise_for_status()
            body = await self._read_limited_response_body(response)

        audio = self._decode_pcm16(body)
        yield audio

    async def _read_limited_response_body(self, response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            if not chunk:
                continue
            total += len(chunk)
            if total > self._max_response_bytes:
                raise SarvamTextToSpeechError("Sarvam response exceeded configured size limit.")
            chunks.append(chunk)
        return b"".join(chunks)

    def _decode_pcm16(self, body: bytes) -> bytes:
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SarvamTextToSpeechError("Sarvam response was not valid JSON.") from exc
        if not isinstance(data, dict):
            raise SarvamTextToSpeechError("Sarvam response must be a JSON object.")
        audios = data.get("audios")
        if not isinstance(audios, list) or not audios:
            raise SarvamTextToSpeechError("Sarvam response did not include audio.")
        first = audios[0]
        if not isinstance(first, str) or not first:
            raise SarvamTextToSpeechError("Sarvam audio payload must be a base64 string.")
        try:
            # binascii.Error (raised on malformed base64) subclasses ValueError.
            raw = base64.b64decode(first, validate=True)
        except ValueError as exc:
            raise SarvamTextToSpeechError("Sarvam audio payload was not valid base64.") from exc
        raw = _strip_wav_header(raw)
        if not raw:
            raise SarvamTextToSpeechError("Sarvam returned no audio.")
        if len(raw) > self._max_audio_bytes:
            raise SarvamTextToSpeechError(
                "Sarvam audio response exceeded configured audio size limit."
            )
        if len(raw) % 2:
            raise SarvamTextToSpeechError("Sarvam returned an incomplete PCM16 sample.")
        return raw

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


MIN_BUFFER_SIZE = 50
DEFAULT_MIN_BUFFER_SIZE = MIN_BUFFER_SIZE
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10.0


class SarvamStreamingTextToSpeech:
    """Sarvam Bulbul TTS over the streaming WebSocket API.

    Streams raw PCM16 chunks as they are generated (first audio ~0.3s) instead of
    waiting for the whole clip, which cuts time-to-first-audio for the phone agent.
    The WebSocket is kept open across synthesize() calls for a media session, so
    LLM sentence/phrase flushes do not pay the handshake tax repeatedly.
    """

    def __init__(
        self,
        *,
        api_key: str,
        speaker: str | None = None,
        model: str = DEFAULT_MODEL,
        target_language_code: str = DEFAULT_TARGET_LANGUAGE,
        pace: float = 1.0,
        temperature: float = 0.6,
        min_buffer_size: int = DEFAULT_MIN_BUFFER_SIZE,
        api_base_url: str = "https://api.sarvam.ai",
        connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        read_timeout_seconds: float | None = None,
        max_audio_bytes: int = DEFAULT_MAX_AUDIO_BYTES,
        retry_max_attempts: int = 1,
        retry_backoff_seconds: float = 0.25,
        retry_max_backoff_seconds: float = 2.0,
    ) -> None:
        if connect_timeout_seconds <= 0:
            raise ValueError("connect_timeout_seconds must be positive.")
        if read_timeout_seconds is not None and read_timeout_seconds <= 0:
            raise ValueError("read_timeout_seconds must be positive.")
        if max_audio_bytes <= 0:
            raise ValueError("max_audio_bytes must be positive.")
        if pace <= 0:
            raise ValueError("pace must be positive.")
        if temperature <= 0:
            raise ValueError("temperature must be positive.")
        if min_buffer_size < MIN_BUFFER_SIZE:
            raise ValueError(f"min_buffer_size must be at least {MIN_BUFFER_SIZE}.")
        if retry_max_attempts < 1:
            raise ValueError("retry_max_attempts must be at least 1.")
        self._retry_max_attempts = retry_max_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._retry_max_backoff_seconds = retry_max_backoff_seconds
        self._api_key = _required_config_string(api_key, "api_key")
        self._speaker = (
            speaker.strip().lower() if isinstance(speaker, str) and speaker.strip() else None
        )
        self._model = _required_config_string(model, "model")
        self._target_language_code = _required_config_string(
            target_language_code, "target_language_code"
        )
        self._pace = pace
        self._temperature = temperature
        self._min_buffer_size = min_buffer_size
        self._connect_timeout_seconds = connect_timeout_seconds
        self._read_timeout_seconds = (
            min(connect_timeout_seconds, 10.0)
            if read_timeout_seconds is None
            else read_timeout_seconds
        )
        self._max_audio_bytes = max_audio_bytes
        base = _required_config_string(api_base_url, "api_base_url").rstrip("/")
        ws_base = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        # The model id contains a colon (bulbul:v3); a raw colon is valid in a query
        # and is what the live API accepts, so build the query without percent-encoding.
        self._url = f"{ws_base}/text-to-speech/ws?model={self._model}&send_completion_event=true"
        self._ssl_context = ssl.create_default_context(cafile=certifi.where())
        self._connection: Any | None = None
        self._connection_lock = asyncio.Lock()
        self._synthesis_lock = asyncio.Lock()
        self._configured_audio_format: PlivoAudioFormat | None = None

    async def start(self) -> None:
        await self._ensure_connected(PlivoAudioFormat.L16_16KHZ)

    async def synthesize(
        self,
        *,
        text: str,
        audio_format: PlivoAudioFormat,
    ) -> AsyncIterator[bytes]:
        if audio_format is not PlivoAudioFormat.L16_16KHZ:
            raise ValueError("Sarvam adapter is configured for PCM16 16kHz Plivo streams.")
        clean_text = text.strip()
        if not clean_text:
            raise SarvamTextToSpeechError("Sarvam text must not be blank.")

        async with self._synthesis_lock:
            connection = await self._ensure_connected(audio_format)
            try:
                await connection.send(json.dumps({"type": "text", "data": {"text": clean_text}}))
                await connection.send(json.dumps({"type": "flush"}))

                total = 0
                message_iterator = connection.__aiter__()
                while True:
                    raw_message = await self._read_next_message(message_iterator)
                    if not isinstance(raw_message, str):
                        continue
                    message = json.loads(raw_message)
                    if not isinstance(message, dict):
                        continue
                    message_type = message.get("type")
                    raw_data = message.get("data")
                    data = raw_data if isinstance(raw_data, dict) else {}
                    if message_type == "audio":
                        chunk = self._decode_audio_chunk(data)
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > self._max_audio_bytes:
                            raise SarvamTextToSpeechError(
                                "Sarvam audio response exceeded configured audio size limit."
                            )
                        yield chunk
                    elif message_type == "event" and data.get("event_type") == "final":
                        return
                    elif message_type == "error":
                        raise SarvamTextToSpeechError(
                            f"Sarvam streaming error: {data.get('message') or 'unknown error'}."
                        )
            except BaseException:
                await self._drop_connection(connection)
                raise

    async def _read_next_message(self, message_iterator: AsyncIterator[Any]) -> Any:
        try:
            return await asyncio.wait_for(
                anext(message_iterator),
                timeout=self._read_timeout_seconds,
            )
        except TimeoutError as exc:
            raise SarvamTextToSpeechError(
                "Sarvam streaming response timed out waiting for audio."
            ) from exc
        except StopAsyncIteration as exc:
            raise SarvamTextToSpeechError(
                "Sarvam streaming response ended before the final event."
            ) from exc

    async def _ensure_connected(self, audio_format: PlivoAudioFormat) -> Any:
        if self._connection is not None:
            return self._connection
        async with self._connection_lock:
            if self._connection is not None:
                return self._connection
            connection = await self._connect_with_retry()
            try:
                await connection.send(
                    json.dumps({"type": "config", "data": self._config_data(audio_format)})
                )
            except BaseException:
                with suppress(Exception):
                    await connection.close()
                raise
            self._connection = connection
            self._configured_audio_format = audio_format
            return connection

    async def _connect_with_retry(self) -> Any:
        attempt = 0
        while True:
            attempt += 1
            try:
                return await websockets.connect(
                    self._url,
                    additional_headers={"api-subscription-key": self._api_key},
                    ssl=self._ssl_context,
                    open_timeout=self._connect_timeout_seconds,
                    max_size=self._max_audio_bytes * 2 + 64 * 1024,
                )
            except (OSError, TimeoutError, websockets.exceptions.WebSocketException) as exc:
                if attempt >= self._retry_max_attempts:
                    raise
                delay = compute_backoff(
                    attempt,
                    base_seconds=self._retry_backoff_seconds,
                    max_seconds=self._retry_max_backoff_seconds,
                )
                logger.warning(
                    "Sarvam streaming connect failed (%s); retry %d/%d in %.2fs.",
                    type(exc).__name__,
                    attempt,
                    self._retry_max_attempts,
                    delay,
                )
                await asyncio.sleep(delay)

    def _config_data(self, audio_format: PlivoAudioFormat) -> dict[str, Any]:
        data: dict[str, Any] = {
            "target_language_code": self._target_language_code,
            "output_audio_codec": "linear16",
            "speech_sample_rate": audio_format.sample_rate,
            "min_buffer_size": self._min_buffer_size,
            "pace": self._pace,
        }
        if self._speaker is not None:
            data["speaker"] = self._speaker
        if "v3" in self._model:
            data["temperature"] = self._temperature
        return data

    def _decode_audio_chunk(self, data: dict[str, Any]) -> bytes:
        payload = data.get("audio")
        if not isinstance(payload, str) or not payload:
            return b""
        try:
            chunk = base64.b64decode(payload, validate=True)
        except ValueError as exc:
            raise SarvamTextToSpeechError("Sarvam audio payload was not valid base64.") from exc
        if len(chunk) % 2:
            raise SarvamTextToSpeechError("Sarvam returned an incomplete PCM16 sample.")
        return chunk

    async def close(self) -> None:
        await self._drop_connection()

    async def _drop_connection(self, connection: Any | None = None) -> None:
        current = self._connection
        if connection is not None and current is not connection:
            return
        self._connection = None
        self._configured_audio_format = None
        if current is not None:
            with suppress(Exception):
                await current.close()


def _strip_wav_header(audio: bytes) -> bytes:
    """Return raw PCM if the payload is a RIFF/WAVE container, else unchanged."""
    if len(audio) >= 12 and audio[:4] == b"RIFF" and audio[8:12] == b"WAVE":
        index = 12
        while index + 8 <= len(audio):
            chunk_id = audio[index : index + 4]
            chunk_size = int.from_bytes(audio[index + 4 : index + 8], "little")
            index += 8
            if chunk_id == b"data":
                return audio[index : index + chunk_size]
            index += chunk_size + (chunk_size & 1)
    return audio


def _required_config_string(value: str, name: str) -> str:
    clean_value = value.strip()
    if not clean_value:
        raise ValueError(f"{name} must not be blank.")
    return clean_value
